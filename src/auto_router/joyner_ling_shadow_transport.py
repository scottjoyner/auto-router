"""Offline-only ASGI Ling shadow transport: no listener or backend configuration.

Must be injected with a separately configured signed-receipt verifier and
DISPOSABLE mock backend. Production imports/routes, GPU, network, routing,
service and model-server state are completely untouched by this module.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from .joyner_ling_receipt_candidate import LingReceipt, LingReceiptDenied

_MAX_BODY = 64 * 1024
_MAX_CHUNK = 1024 * 1024
_MAX_STREAM = 2 * 1024 * 1024
_FIRST_CHUNK_TIMEOUT = 8.0
_NEXT_CHUNK_TIMEOUT = 20.0

# Synthetic-only dependencies; no defaults to the actual localhost Ling port.
Verifier = Callable[[str, bytes], LingReceipt]
MockBackend = Callable[[bytes, LingReceipt], AsyncIterator[bytes]]


def _bearer(headers: list[tuple[bytes, bytes]]) -> str | None:
    auth = [v for k, v in headers if k.lower() == b"authorization"]
    if len(auth) != 1 or len(auth[0]) > 8192:
        return None
    try:
        value = auth[0].decode("ascii", "strict")
    except UnicodeError:
        return None
    if not value.startswith("Bearer ") or not value[7:] or any(
        char.isspace() for char in value[7:]
    ):
        return None
    return value[7:]


async def _reply(send: Callable[..., Awaitable[None]], status: int, detail: bytes) -> None:
    # No request data, bearer token, identity, model prompt or upstream
    # error detail may enter the response body or headers.
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ],
    })
    await send({"type": "http.response.body", "body": detail, "more_body": False})


async def _read_bounded_body(receive: Callable[..., Awaitable[dict[str, Any]]]) -> bytes | None:
    value = bytearray()
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return None
        part = message.get("body", b"")
        if not isinstance(part, bytes) or len(value) + len(part) > _MAX_BODY:
            raise ValueError("request exceeds bounded size")
        value.extend(part)
        if not message.get("more_body", False):
            break
    return bytes(value)


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class LingShadowTransport:
    """No network binder. Expects an authenticated verifier and test backend."""

    def __init__(self, verifier: Verifier, mock_backend: MockBackend) -> None:
        self._verifier = verifier
        self._backend = mock_backend

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            return
        if scope.get("path") != "/v1/chat/completions":
            await _reply(send, 404, b"not found")
            return
        if str(scope.get("method") or "").upper() != "POST":
            await _reply(send, 405, b"method not allowed")
            return
        headers = list(scope.get("headers") or [])
        token = _bearer(headers)
        if token is None:
            await _reply(send, 401, b"unauthorized")
            return
        # Fail closed on duplicated/conflicting or unsupported framing headers.
        content_lengths = [v for k, v in headers if k.lower() == b"content-length"]
        transfers = [v for k, v in headers if k.lower() == b"transfer-encoding"]
        if len(content_lengths) > 1 or transfers:
            await _reply(send, 400, b"bad request")
            return
        if content_lengths:
            try:
                if int(content_lengths[0]) > _MAX_BODY or int(content_lengths[0]) < 0:
                    await _reply(send, 413, b"request too large")
                    return
            except ValueError:
                await _reply(send, 400, b"bad request")
                return
        try:
            body = await _read_bounded_body(receive)
        except ValueError:
            await _reply(send, 413, b"request too large")
            return
        if not body:
            await _reply(send, 400, b"bad request")
            return
        try:
            receipt = self._verifier(token, body)
            if not isinstance(receipt, LingReceipt) or not (
                receipt.verified_signature and receipt.replay_reserved
            ):
                raise LingReceiptDenied("receipt_is_not_a_verified_reservation")
        except Exception:
            await _reply(send, 401, b"unauthorized")
            return

        # The current implementation never uses real services; only a
        # deliberately injected, disposable backend is usable.
        try:
            stream_mode = json.loads(body).get("stream") is True
            iterator = self._backend(body, receipt).__aiter__()
            initial = await asyncio.wait_for(anext(iterator), timeout=_FIRST_CHUNK_TIMEOUT)
            if not isinstance(initial, bytes) or len(initial) > _MAX_CHUNK:
                raise ValueError("unbounded upstream")
        except (Exception, asyncio.TimeoutError):
            await _reply(send, 502, b"upstream unavailable")
            return

        response_type = (
            b"text/event-stream; charset=utf-8" if stream_mode
            else b"application/json"
        )
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", response_type),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })

        next_task: asyncio.Task[bytes] | None = None
        disconnect_task: asyncio.Task[dict[str, Any]] | None = None
        sent = 0
        try:
            current = initial
            while True:
                if sent + len(current) > _MAX_STREAM:
                    break
                sent += len(current)
                await send({"type": "http.response.body", "body": current, "more_body": True})
                next_task = asyncio.create_task(anext(iterator))
                disconnect_task = asyncio.create_task(receive())
                done, _ = await asyncio.wait(
                    {next_task, disconnect_task},
                    timeout=_NEXT_CHUNK_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect_task in done and (
                    (await disconnect_task).get("type") == "http.disconnect"
                ):
                    return
                if next_task not in done:
                    # No trusted upstream data; don't fabricate completion.
                    break
                try:
                    current = await next_task
                except StopAsyncIteration:
                    break
                except Exception:
                    break
                if not isinstance(current, bytes) or len(current) > _MAX_CHUNK:
                    break
                await _cancel_task(disconnect_task)
                next_task = None
                disconnect_task = None
        finally:
            await _cancel_task(next_task)
            await _cancel_task(disconnect_task)
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()
        await send({"type": "http.response.body", "body": b"", "more_body": False})
