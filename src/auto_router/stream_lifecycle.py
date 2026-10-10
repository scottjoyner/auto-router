from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
from typing import Any

from auto_router.event_outbox import OutboxEvent
from auto_router.providers import ProviderError, ProviderStreamResponse
from auto_router.route_events import ensure_event_outbox


class StreamCancelledError(RuntimeError):
    pass


class StreamUsageCollector:
    def __init__(self) -> None:
        self.buffer = ""
        self.usage: dict[str, int] = {}
        self.bytes_sent = 0
        self.chunks_sent = 0
        self.terminal_status: str | None = None
        self.terminal_evidence: str | None = None

    def feed(self, chunk: bytes) -> None:
        self.bytes_sent += len(chunk)
        self.chunks_sent += 1
        self.buffer += chunk.decode("utf-8", errors="ignore")
        if len(self.buffer) > 262_144:
            self.buffer = self.buffer[-262_144:]
        lines = self.buffer.split("\n")
        self.buffer = lines.pop() if lines else ""
        for line in lines:
            self._line(line.strip())

    def finish(self) -> None:
        if self.buffer.strip():
            self._line(self.buffer.strip())
        self.buffer = ""

    def _line(self, line: str) -> None:
        if not line:
            return
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            return
        if line == "[DONE]":
            if self.terminal_status != "failed":
                self.terminal_status = "completed"
                self.terminal_evidence = "openai_sse_done"
            return
        if not line.startswith("{"):
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        # OpenAI Responses emits a typed terminal rather than requiring
        # [DONE]. Only protocol terminal markers may prove stream completion.
        # A clean TCP EOF or HTTP 200 cannot: providers can truncate a stream.
        kind = payload.get("type")
        if kind == "response.completed" and self.terminal_status != "failed":
            self.terminal_status = "completed"
            self.terminal_evidence = "responses_completed"
        elif kind in {"response.failed", "response.incomplete"}:
            self.terminal_status = "failed"
            self.terminal_evidence = (
                "responses_failed" if kind == "response.failed"
                else "responses_incomplete"
            )
        candidates = [payload.get("usage")]
        response = payload.get("response")
        if isinstance(response, dict):
            candidates.append(response.get("usage"))
        for usage in candidates:
            normalized = self._normalize_usage(usage)
            if normalized:
                self.usage.update(normalized)

    @staticmethod
    def _normalize_usage(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        aliases = {
            "prompt_tokens": ("prompt_tokens", "input_tokens"),
            "completion_tokens": ("completion_tokens", "output_tokens"),
            "total_tokens": ("total_tokens",),
        }
        result: dict[str, int] = {}
        for target, names in aliases.items():
            for name in names:
                raw = value.get(name)
                if isinstance(raw, bool) or raw is None:
                    continue
                try:
                    result[target] = int(raw)
                    break
                except (TypeError, ValueError):
                    continue
        if "total_tokens" not in result and (
            "prompt_tokens" in result or "completion_tokens" in result
        ):
            result["total_tokens"] = result.get("prompt_tokens", 0) + result.get(
                "completion_tokens", 0
            )
        return result


def _idempotency_key(request: Any) -> str:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    return str(metadata.get("idempotency_key") or "").strip()


def _transition(state: Any, request: Any, status: str, **kwargs: Any) -> None:
    key = _idempotency_key(request)
    ledger = getattr(state, "request_idempotency", None)
    if key and ledger is not None:
        ledger.transition(key, status, **kwargs)


def _stream_context(request: Any) -> dict[str, Any]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    context = metadata.get("_stream_record_context")
    return context if isinstance(context, dict) else {}


def _honest_estimate(estimate: Any, usage: dict[str, int]) -> Any:
    if usage:
        return estimate
    return SimpleNamespace(
        input_tokens=getattr(estimate, "input_tokens", 0),
        total_tokens=0,
        dimensions=getattr(estimate, "dimensions", {}),
    )


def _route_config_identity(value: Any) -> str | None:
    """A planned config ID, not attestation from the running model process."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else None


def _enqueue_lifecycle_event(
    state: Any,
    request: Any,
    *,
    provider: str,
    model: str,
    selected_candidate: Any,
    status: str,
    usage_status: str,
    collector: StreamUsageCollector,
    latency_ms: int,
    error: BaseException | None,
) -> None:
    # The incoming request's metadata is not model-execution evidence:
    # callers can supply runtime_instance_id, model_instance_id and claim_id.
    # The route candidate comes from the router's own configuration, though
    # it is still only planned selection, not a provider-attested process ID.
    chosen_provider = selected_candidate.provider
    chosen_model = selected_candidate.model
    ensure_event_outbox(state).enqueue(
        OutboxEvent(
            event_type=f"router.stream.{status}",
            idempotency_key=f"router.stream.{status}:{request.request_id}",
            payload={
                "request_id": request.request_id,
                "task_id": getattr(request, "task_id", None),
                # TODO: link a claim only after executor-auth supplies an
                # independently verified, server-bound receipt. A caller's
                # assistx_executor.claim_id does NOT prove authorization.
                "authenticated_claim_binding": False,
                "claim_id": None,  # retained schema field, NEVER caller's claim
                "provider": provider,
                "model": model,
                "status": status,
                "acceptance_state": (
                    "completed" if status == "completed" else "possibly_accepted"
                ),
                # Whitelisted protocol evidence only; no content or secrets.
                # EOF after HTTP headers alone must never mean model completion.
                "terminal_evidence": (
                    "client_cancelled" if status == "cancelled"
                    else "transport_exception" if status == "failed"
                    else collector.terminal_evidence or "eof_without_terminal"
                ),
                "usage_status": usage_status,
                "usage": collector.usage,
                "bytes_sent": collector.bytes_sent,
                "chunks_sent": collector.chunks_sent,
                "latency_ms": latency_ms,
                "error_type": type(error).__name__ if error else None,
                # Raw exception text may contain upstream response bodies,
                # URLs or API credentials: never publish in shared outbox.
                "error_message": None,
                "runtime_projection_generation": None,
                "runtime_instance_id": _route_config_identity(
                    getattr(chosen_provider, "runtime_instance_id", None)
                ),
                "model_instance_id": _route_config_identity(
                    getattr(chosen_model, "model_instance_id", None)
                ),
                "node_id": _route_config_identity(
                    getattr(chosen_provider, "node_id", None)
                ),
                "instance_identity_source": "configured_route_not_runtime_attestation",
            },
        )
    )


def install_stream_lifecycle(main_module: Any) -> None:
    if getattr(main_module, "_strict_stream_lifecycle_installed", False):
        return

    state = main_module.state
    original_dispatch = main_module._dispatch
    original_dispatch_stream = main_module._dispatch_stream
    original_record_usage = main_module._record_usage

    async def dispatch(provider: Any, candidate: Any, request: Any, route_plan: Any = None):
        _transition(state, request, "upstream_started")
        try:
            return await original_dispatch(
                provider,
                candidate,
                request,
                route_plan=route_plan,
            )
        except ProviderError as exc:
            if exc.status_code is not None:
                _transition(
                    state,
                    request,
                    "in_progress",
                    status_code=exc.status_code,
                    detail="upstream returned a definitive HTTP rejection",
                )
            else:
                _transition(
                    state,
                    request,
                    "possibly_accepted",
                    detail=str(exc),
                )
            raise
        except asyncio.CancelledError:
            _transition(
                state,
                request,
                "possibly_accepted",
                detail="dispatch timed out or was cancelled after upstream start",
            )
            raise
        except BaseException as exc:
            _transition(
                state,
                request,
                "possibly_accepted",
                detail=f"dispatch interrupted: {type(exc).__name__}",
            )
            raise

    async def dispatch_stream(
        provider: Any,
        candidate: Any,
        request: Any,
        route_plan: Any = None,
    ) -> ProviderStreamResponse:
        _transition(state, request, "upstream_started")
        try:
            response = await original_dispatch_stream(
                provider,
                candidate,
                request,
                route_plan=route_plan,
            )
        except ProviderError as exc:
            if exc.status_code is not None:
                _transition(
                    state,
                    request,
                    "in_progress",
                    status_code=exc.status_code,
                    detail="upstream returned a definitive HTTP rejection",
                )
            else:
                _transition(
                    state,
                    request,
                    "possibly_accepted",
                    detail=str(exc),
                )
            raise
        except asyncio.CancelledError:
            _transition(
                state,
                request,
                "possibly_accepted",
                detail="stream establishment timed out after upstream start",
            )
            raise

        owner = main_module._owner(candidate)
        # Capture the provider iterator before replacing response.body. Referencing
        # response.body from the wrapper would recurse into the wrapper generator.
        upstream_body = response.body
        # Base _execute releases its in-flight mark when headers arrive. Add one
        # matching mark that remains until the response iterator actually closes.
        state.policy_engine.mark_inflight_start(owner)
        collector = StreamUsageCollector()
        stream_started = time.perf_counter()
        stream_started_at_ms = int(time.time() * 1000)
        finalized = False

        async def finalize(
            status: str,
            error: BaseException | None = None,
        ) -> None:
            nonlocal finalized
            if finalized:
                return
            finalized = True
            collector.finish()
            state.policy_engine.mark_inflight_end(owner)
            latency_ms = int((time.perf_counter() - stream_started) * 1000)
            state.policy_engine.mark_latency(owner, latency_ms)
            context = _stream_context(request)
            estimate = context.get("estimate")
            if estimate is None:
                estimate = await asyncio.to_thread(
                    state.quota.estimate,
                    candidate.model,
                    request.raw_body,
                )
            usage_status = "reported" if collector.usage else "pending"
            if status == "completed":
                state.circuits.record_success(owner)
                _transition(
                    state,
                    request,
                    "completed",
                    status_code=response.status_code,
                )
            else:
                await asyncio.to_thread(
                    state.quota.release,
                    candidate.provider,
                    candidate.model,
                    estimate,
                )
                if status == "failed":
                    state.circuits.record_failure(owner, str(error or "stream failed"))
                _transition(
                    state,
                    request,
                    "possibly_accepted",
                    status_code=499 if status == "cancelled" else response.status_code,
                    detail=str(error or status),
                )
            record_error: Exception | None
            if status == "cancelled":
                record_error = StreamCancelledError("client cancelled response stream")
            elif status in {"failed", "incomplete"}:
                record_error = (
                    error if isinstance(error, Exception)
                    else RuntimeError("provider_stream_unverified_or_incomplete")
                )
            else:
                record_error = None
            await asyncio.to_thread(
                original_record_usage,
                request,
                response.provider,
                response.model,
                str(context.get("stage") or "final"),
                _honest_estimate(estimate, collector.usage),
                499 if status == "cancelled" else response.status_code,
                latency_ms,
                collector.usage,
                record_error,
                context.get("gateway_metadata"),
                int(context.get("started_at_ms") or stream_started_at_ms),
                int(time.time() * 1000),
            )
            await asyncio.to_thread(
                _enqueue_lifecycle_event,
                state,
                request,
                provider=response.provider,
                model=response.model,
                selected_candidate=candidate,
                status=status,
                usage_status=usage_status,
                collector=collector,
                latency_ms=latency_ms,
                error=error,
            )

        async def body():
            completed = False
            try:
                async for chunk in upstream_body:
                    collector.feed(chunk)
                    yield chunk
                completed = True
            except asyncio.CancelledError as exc:
                await finalize("cancelled", exc)
                raise
            except BaseException as exc:
                await finalize("failed", exc)
                raise
            finally:
                if completed:
                    if collector.terminal_status == "completed":
                        await finalize("completed")
                    else:
                        # Iterator EOF without a terminal event is not evidence
                        # that a shared model completed the requested work.
                        await finalize("incomplete")
                elif not finalized:
                    await finalize("cancelled")

        response.body = body()
        return response

    def record_usage(
        request: Any,
        provider: str,
        model: str,
        stage: str,
        estimate: Any,
        status_code: int | None,
        latency_ms: int,
        usage: dict[str, int] | None = None,
        error: Exception | None = None,
        gateway_metadata: dict[str, Any] | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if getattr(request, "stream", False) and error is None:
            metadata = request.metadata if isinstance(request.metadata, dict) else {}
            metadata["_stream_record_context"] = {
                "provider": provider,
                "model": model,
                "stage": stage,
                "estimate": estimate,
                "gateway_metadata": gateway_metadata,
                "started_at_ms": started_at_ms,
                "handshake_latency_ms": latency_ms,
            }
            request.metadata = metadata
            return
        original_record_usage(
            request,
            provider,
            model,
            stage,
            estimate,
            status_code,
            latency_ms,
            usage,
            error,
            gateway_metadata,
            started_at_ms,
            ended_at_ms,
            *args,
            **kwargs,
        )

    main_module._dispatch = dispatch
    main_module._dispatch_stream = dispatch_stream
    main_module._record_usage = record_usage
    main_module._strict_stream_lifecycle_installed = True
