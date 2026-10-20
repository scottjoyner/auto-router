"""Draft GPU-owner data-plane guard, disconnected from the live fleet.

The guard requires an independently authenticated/fenced authority and an
isolated host-local model listener. No production authority or isolation proof
exists in this repository; create_gpu_owner_app() is NOT a deployable route.

This first slice supports small, non-streaming completions only. An ASGI client
disconnect or a revoked authority lease cancels the proxy task and requests
backend abort; raw llama.cpp still requires a supervised isolation and worker
termination design before production dispatch can be claimed safe.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


class GpuOwnerRejected(RuntimeError):
    """The authority denied ownership or the lease became stale."""


class GpuOwnerUnavailable(RuntimeError):
    """Authority or backend control is unavailable: fail closed."""


@dataclass(frozen=True)
class OwnerLease:
    """Fields are minted by the trusted authority, never client headers."""

    group_id: str
    model_id: str
    tenant_id: str
    actor_id: str
    root_request_id: str
    lease_id: str
    epoch: int

    def validate(self, group_id: str, model_id: str) -> None:
        if self.group_id != group_id or self.model_id != model_id:
            raise GpuOwnerRejected("lease bound to a different GPU or model")
        if self.epoch <= 0 or any(not getattr(self, key) for key in (
            "tenant_id", "actor_id", "root_request_id", "lease_id"
        )):
            raise GpuOwnerRejected("incomplete owner-bound fencing lease")


class OwnerAuthority(Protocol):
    async def acquire(
        self, *, delegation: str, group_id: str, model_id: str
    ) -> OwnerLease: ...
    async def renew_and_assert_current(self, lease: OwnerLease) -> None: ...
    async def release(self, lease: OwnerLease) -> None: ...


class LocalModelBackend(Protocol):
    """An isolated local worker whose active work can be terminated."""

    async def complete(self, *, model_id: str, payload: dict[str, Any]) -> dict: ...
    async def abort(self) -> None: ...


@dataclass(frozen=True)
class GpuOwnerBinding:
    group_id: str
    model_id: str
    local_backend_url: str
    max_tokens: int = 64
    max_body_bytes: int = 8192

    def __post_init__(self) -> None:
        parsed = urlsplit(self.local_backend_url)
        try:
            host = ipaddress.ip_address(parsed.hostname or "")
        except ValueError as exc:
            raise ValueError("backend must use a literal loopback address") from exc
        if (
            parsed.scheme != "http"
            or not host.is_loopback
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.port
        ):
            raise ValueError("backend must be isolated on an HTTP loopback listener")
        if not self.group_id or not self.model_id:
            raise ValueError("GPU identity and exact model ID are mandatory")
        if self.max_tokens <= 0 or self.max_tokens > 256:
            raise ValueError("GPU owner pilot must use bounded output")
        if self.max_body_bytes < 1024 or self.max_body_bytes > 32768:
            raise ValueError("GPU owner pilot request size is invalid")


class GpuOwnerGuard:
    """Draft single-owner ingress with authority-backed periodic validation."""

    def __init__(
        self,
        *,
        binding: GpuOwnerBinding,
        authority: OwnerAuthority,
        backend: LocalModelBackend,
        check_interval_s: float = 0.05,
        request_timeout_s: float = 5.0,
    ):
        if not (0.005 <= check_interval_s <= 1.0):
            raise ValueError("invalid authority recheck interval")
        if not (0.1 <= request_timeout_s <= 30):
            raise ValueError("invalid bounded request timeout")
        self.binding = binding
        self.authority = authority
        self.backend = backend
        self.check_interval_s = check_interval_s
        self.request_timeout_s = request_timeout_s
        self._active = asyncio.Lock()

    async def execute(self, *, delegation: str, payload: dict[str, Any]) -> dict:
        """Reject unknown owners and fail closed before or during model execution."""
        if not isinstance(payload, dict) or payload.get("model") != self.binding.model_id:
            raise GpuOwnerRejected("model not admitted for GPU-owner pilot")
        if payload.get("stream") is not False:
            raise GpuOwnerRejected("streaming requires separate lifecycle proof")
        max_tokens = payload.get("max_tokens")
        if type(max_tokens) is not int or not 1 <= max_tokens <= self.binding.max_tokens:
            raise GpuOwnerRejected("output limit is not approved")
        if set(payload) - {"model", "stream", "max_tokens", "messages", "temperature"}:
            raise GpuOwnerRejected("unapproved chat parameters")
        temperature = payload.get("temperature", 0)
        if (
            type(temperature) not in (float, int)
            or not 0 <= temperature <= 1
        ):
            raise GpuOwnerRejected("unapproved sampling temperature")
        messages = payload.get("messages")
        if (
            not isinstance(messages, list)
            or len(messages) < 1
            or len(messages) > 5
            or any(
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message.get("role") != "user"
                or not isinstance(message.get("content"), str)
                or not 1 <= len(message["content"]) <= 2048
                for message in messages
            )
        ):
            raise GpuOwnerRejected("invalid bounded chat request")
        if not delegation or len(delegation) > 8192:
            raise GpuOwnerRejected("trusted delegation is required")
        if self._active.locked():
            raise GpuOwnerRejected("physical GPU owner is already executing")

        # The local lock is defense-in-depth, NOT a substitute for an external,
        # durable authority that fences across replicas and proxy restarts.
        async with self._active:
            try:
                lease = await self.authority.acquire(
                    delegation=delegation,
                    group_id=self.binding.group_id,
                    model_id=self.binding.model_id,
                )
            except GpuOwnerRejected:
                raise
            except Exception as exc:
                raise GpuOwnerUnavailable("trusted owner authority unavailable") from exc

            task: asyncio.Task[dict] | None = None
            try:
                lease.validate(self.binding.group_id, self.binding.model_id)
                await self._verify_authority(lease)
                task = asyncio.create_task(
                    self.backend.complete(model_id=self.binding.model_id, payload=payload)
                )
                loop = asyncio.get_running_loop()
                expires = loop.time() + self.request_timeout_s
                while not task.done():
                    if loop.time() >= expires:
                        raise GpuOwnerUnavailable("bounded GPU owner request timed out")
                    await asyncio.wait({task}, timeout=min(
                        self.check_interval_s, max(0, expires - loop.time())
                    ))
                    # Validate after every wakeup, INCLUDING the completion
                    # wakeup, so revoked generation is never marked completed.
                    await self._verify_authority(lease)
                result = await task
                await self._verify_authority(lease)
                if not isinstance(result, dict) or result.get("model") != self.binding.model_id:
                    raise GpuOwnerUnavailable("backend model identity mismatch")
                choices = result.get("choices")
                if not isinstance(choices, list) or len(choices) != 1:
                    raise GpuOwnerUnavailable("backend terminal proof is missing")
                choice = choices[0]
                message = choice.get("message", {}) if isinstance(choice, dict) else {}
                usage = result.get("usage", {})
                if (
                    not isinstance(choice, dict)
                    or choice.get("finish_reason") != "stop"
                    or not isinstance(message, dict)
                    or not isinstance(message.get("content"), str)
                    or not message["content"]
                    or not isinstance(usage, dict)
                    or type(usage.get("completion_tokens")) is not int
                    or usage["completion_tokens"] <= 0
                ):
                    raise GpuOwnerUnavailable("backend terminal proof is incomplete")
                return result
            except BaseException:
                if task is not None:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass
                    # The backend must implement real cancellation/termination.
                    # This call alone is not independent proof it succeeded.
                    try:
                        await self.backend.abort()
                    except Exception:
                        pass
                raise
            finally:
                try:
                    await self.authority.release(lease)
                except Exception as exc:
                    # Unknown custody after failed release is a hard NO-GO.
                    # Never turn the response into a success silently.
                    raise GpuOwnerUnavailable("owner lease release unconfirmed") from exc

    async def _verify_authority(self, lease: OwnerLease) -> None:
        try:
            await self.authority.renew_and_assert_current(lease)
        except GpuOwnerRejected:
            raise
        except Exception as exc:
            raise GpuOwnerUnavailable("GPU fencing authority unavailable") from exc


def create_gpu_owner_app(
    *,
    binding: GpuOwnerBinding | None = None,
    authority: OwnerAuthority | None = None,
    backend: LocalModelBackend | None = None,
) -> FastAPI:
    """Build a *prototype* ASGI ingress. No working production authority exists.

    Requires explicit non-default dependency injection, so importing this
    module cannot expose the four real GPU services or load a model.
    """
    app = FastAPI(title="GPU Owner Proxy - NOT RELEASED", docs_url=None, redoc_url=None)
    guard = (
        GpuOwnerGuard(binding=binding, authority=authority, backend=backend)
        if binding is not None and authority is not None and backend is not None
        else None
    )

    @app.get("/health")
    async def health() -> JSONResponse:
        # HTTP 503 is intentional: no current owner proxy installation has
        # proved worker isolation, durable epoch enforcement or user custody.
        # Returning 200 here would falsely qualify it for fleet admission.
        return JSONResponse({
            "status": "no_go",
            "production_dispatch_admitted": False,
            "gpu_worker_isolation_verified": False,
            "durable_fencing_proven": False,
        }, status_code=503, headers={"Cache-Control": "no-store"})

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        if guard is None:
            raise HTTPException(503, "GPU owner authority / backend unavailable")
        token = request.headers.get("authorization", "")
        if not token.startswith("Bearer ") or len(token) < 15:
            raise HTTPException(401, "trusted delegation missing")
        if request.headers.get("content-length"):
            try:
                size = int(request.headers["content-length"])
            except ValueError:
                raise HTTPException(400, "invalid payload length") from None
            if size > guard.binding.max_body_bytes:
                raise HTTPException(413, "GPU pilot payload exceeds size limit")
        body = await request.body()
        if len(body) > guard.binding.max_body_bytes:
            raise HTTPException(413, "GPU pilot payload exceeds size limit")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "invalid JSON") from None
        try:
            result = await guard.execute(delegation=token[7:], payload=payload)
        except GpuOwnerRejected:
            raise HTTPException(403, "GPU owner request not authorized") from None
        except GpuOwnerUnavailable:
            raise HTTPException(503, "GPU owner fencing unavailable") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return app
