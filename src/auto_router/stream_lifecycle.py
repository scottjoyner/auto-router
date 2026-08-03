from __future__ import annotations

import asyncio
import json
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
        if not line or line == "[DONE]" or not line.startswith("{"):
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
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


def _enqueue_lifecycle_event(
    state: Any,
    request: Any,
    *,
    provider: str,
    model: str,
    status: str,
    usage_status: str,
    collector: StreamUsageCollector,
    latency_ms: int,
    error: BaseException | None,
) -> None:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    ensure_event_outbox(state).enqueue(
        OutboxEvent(
            event_type=f"router.stream.{status}",
            idempotency_key=f"router.stream.{status}:{request.request_id}",
            payload={
                "request_id": request.request_id,
                "task_id": getattr(request, "task_id", None),
                "claim_id": (
                    metadata.get("assistx_executor", {}).get("claim_id")
                    if isinstance(metadata.get("assistx_executor"), dict)
                    else None
                ),
                "provider": provider,
                "model": model,
                "status": status,
                "acceptance_state": (
                    "completed" if status == "completed" else "possibly_accepted"
                ),
                "usage_status": usage_status,
                "usage": collector.usage,
                "bytes_sent": collector.bytes_sent,
                "chunks_sent": collector.chunks_sent,
                "latency_ms": latency_ms,
                "error_type": type(error).__name__ if error else None,
                "error_message": str(error)[:1000] if error else None,
                "runtime_projection_generation": metadata.get(
                    "runtime_projection_generation"
                ),
                "runtime_instance_id": metadata.get("runtime_instance_id"),
                "model_instance_id": metadata.get("model_instance_id"),
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
            elif status == "failed":
                record_error = (
                    error if isinstance(error, Exception) else RuntimeError(str(error))
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
                status=status,
                usage_status=usage_status,
                collector=collector,
                latency_ms=latency_ms,
                error=error,
            )

        async def body():
            completed = False
            try:
                async for chunk in response.body:
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
                    await finalize("completed")
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
