#!/usr/bin/env python3
"""Joyner-only signed one-use Ling streaming witness; zero persistent listeners.

Uses ASGI transport, the existing Ed25519 receipt verifier and the *resident*
Ling model on loopback. Sender identity, Ed25519 private key and SQLite store
are synthetic/ephemeral: never a fleet production authorization witness.
Every attempted request and backend outcome is audited on NAS before dispatch.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sys
import tempfile
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joyner_ling_guarded_shadow import (
    ALIAS,
    LING_MODEL_SHA256,
    Audit,
    check_host,
    verify_ling_backend,
)

from auto_router.joyner_ling_receipt_candidate import LingReceiptDenied, validate_and_reserve
from auto_router.joyner_ling_shadow_transport import LingShadowTransport

MODEL_SERVER = ("127.0.0.1", 1236)
PRINCIPAL = "synthetic-ling-witness"
GENERATION = 77
MAX_STREAM_BYTES = 128_000
PROMPT = "Reply with the exact uppercase word READY and nothing else."


def b64(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def prepare_signed(body: bytes, private: Ed25519PrivateKey, *, timestamp: int, jti: str) -> str:
    header = {"alg": "EdDSA", "typ": "assistx-joyner-ling+jwt", "kid": "joyner-ling-v1"}
    claims = {
        "iss": "assistx",
        "aud": "joyner-ling",
        "scopes": ["inference:ling"],
        "model_alias": ALIAS,
        "max_attempts": 1,
        "max_output_tokens": 96,
        "iat": timestamp - 1,
        "nbf": timestamp - 1,
        "exp": timestamp + 60,
        "principal_id": PRINCIPAL,
        "task_id": "synthetic-task-1",
        "claim_id": "synthetic-claim-1",
        "agent_id": "synthetic-witness",
        "jti": jti,
        "projection_generation": GENERATION,
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "model_sha256": LING_MODEL_SHA256,
    }
    unsigned = (b64(header) + "." + b64(claims)).encode()
    return (
        unsigned.decode()
        + "."
        + base64.urlsafe_b64encode(private.sign(unsigned)).decode().rstrip("=")
    )


def synthetic_body() -> bytes:
    return json.dumps(
        {
            "model": ALIAS,
            "messages": [{"role": "user", "content": PROMPT}],
            "stream": True,
            "max_tokens": 96,
            "temperature": 0,
            "seed": 7,
        },
        separators=(",", ":"),
    ).encode()


async def stream_backend(body: bytes, _, *, info: dict):
    """Fixed loopback HTTP/1.1 SSE bridge; bounded queue and exact byte chunks."""
    queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=8)
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def producer():
        conn = HTTPConnection(*MODEL_SERVER, timeout=12)
        total = 0
        try:
            conn.request(
                "POST",
                "/v1/chat/completions",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            )
            response = conn.getresponse()
            if response.status != 200:
                raise RuntimeError("ling_backend_status_not_200")
            # HTTPResponse.read1 returns available bounded transport bytes.
            while not stop.is_set():
                piece = response.read1(4096)
                if not piece:
                    break
                total += len(piece)
                if total > MAX_STREAM_BYTES:
                    raise ValueError("ling_stream_exceeds_bound")
                asyncio.run_coroutine_threadsafe(queue.put(piece), loop).result(timeout=6)
            info["backend_bytes_read"] = total
        except BaseException as exc:
            info["backend_error_class"] = type(exc).__name__
            asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result(timeout=6)
        finally:
            conn.close()
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result(timeout=6)

    task = asyncio.create_task(asyncio.to_thread(producer))
    try:
        while True:
            data = await asyncio.wait_for(queue.get(), timeout=20)
            if data is None:
                break
            if isinstance(data, BaseException):
                raise RuntimeError("backend transport failed") from data
            yield data
    finally:
        stop.set()
        # Any open socket/read times out; do not cancel shared live model service.
        try:
            await asyncio.wait_for(task, timeout=15)
        except (TimeoutError, Exception):
            pass


async def run_asgi(
    app, body: bytes, token: str | None, audit: Audit, kind: str, backend_info: dict
):
    """No ASGI listening socket; simulate an HTTP request to the real gateway."""
    # Token presence is not authorization. Record a provisional deny and only
    # elevate the audit outcome when the Ed25519 verifier reserved a one-use JTI.
    row = audit.pending("signed_stream", False, hashlib.sha256(body).hexdigest())
    row["credential_present"] = token is not None
    backend_info["reservation_verified"] = False
    backend_info["reservation_provenance"] = None
    backend_info["backend_bytes_read"] = 0
    events = []
    received = False

    async def receive():
        nonlocal received
        if received:
            # Streaming handler polls disconnect after first request message.
            await asyncio.sleep(60)
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(event):
        events.append(event)

    started = time.perf_counter()
    try:
        headers = [(b"content-type", b"application/json")]
        if token is not None:
            headers.append((b"authorization", ("Bearer " + token).encode()))
        await asyncio.wait_for(
            app(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/v1/chat/completions",
                    "headers": headers,
                },
                receive,
                send,
            ),
            timeout=35,
        )
        status = next(x["status"] for x in events if x["type"] == "http.response.start")
        chunks = [x.get("body", b"") for x in events if x["type"] == "http.response.body"]
        streamed = b"".join(chunks)
        if len(streamed) > MAX_STREAM_BYTES:
            raise ValueError("gateway_stream_over_limit")
        metadata = {
            "backend_bytes_read": backend_info.get("backend_bytes_read", 0),
            "streamed_chunks": len(chunks),
            "streamed_bytes": len(streamed),
            "stream_sha256": hashlib.sha256(streamed).hexdigest(),
            "stream_done_marker_observed": b"data: [DONE]" in streamed,
            "stream_content_not_saved": True,
        }
        row["auth_verdict"] = "pass" if backend_info["reservation_verified"] else "denied"
        row["signed_reservation_provenance"] = backend_info["reservation_provenance"]
        audit.finish(
            row,
            status,
            "stream_completed" if status == 200 else "stream_denied",
            round((time.perf_counter() - started) * 1000, 2),
            metadata,
        )
        return {"case": kind, "http_status": status, **metadata}
    except Exception as exc:
        row["auth_verdict"] = "pass" if backend_info["reservation_verified"] else "denied"
        row["signed_reservation_provenance"] = backend_info["reservation_provenance"]
        audit.finish(
            row,
            503,
            "request_failed",
            round((time.perf_counter() - started) * 1000, 2),
            {"error_class": type(exc).__name__, "stream_content_not_saved": True},
        )
        raise


async def main():
    check_host()
    identity = verify_ling_backend()
    audit = Audit()
    audit.document["backend_identity"] = identity
    audit.document["signed_receipt_mode"] = "synthetic_ephemeral_trust_only"
    audit._persist()
    private = Ed25519PrivateKey.generate()
    now = int(time.time())
    body = synthetic_body()
    signed = prepare_signed(body, private, timestamp=now, jti=secrets.token_hex(20))
    result = []
    with tempfile.TemporaryDirectory(
        prefix="ling-signed-witness-", dir="/home/joyner/fleet-gpu/research-admission"
    ) as temp:
        db = Path(temp) / "receipts.sqlite"
        info = {"backend_calls": 0, "backend_bytes_read": 0}

        def verify(token, request_bytes):
            receipt = validate_and_reserve(
                token,
                request_bytes,
                public_key=private.public_key(),
                trusted_caller_principal=PRINCIPAL,
                trusted_projection_generation=GENERATION,
                trusted_running_model_sha256=LING_MODEL_SHA256,
                database_path=db,
                now=now,
            )
            info["reservation_verified"] = True
            info["reservation_provenance"] = {
                "synthetic_principal": receipt.principal_id,
                "synthetic_generation": receipt.projection_generation,
                "jti_sha256": hashlib.sha256(receipt.jti.encode()).hexdigest(),
                "signed_request_sha256": receipt.request_sha256,
            }
            return receipt

        async def backend(b, receipt):
            info["backend_calls"] += 1
            async for part in stream_backend(b, receipt, info=info):
                yield part

        app = LingShadowTransport(verify, backend)
        for name, key in (("authorized_first", signed), ("replay", signed), ("anonymous", None)):
            out = await run_asgi(app, body, key, audit, name, info)
            result.append(
                {
                    "case": name,
                    "http_status": out["http_status"],
                    "stream_done_marker_observed": out["stream_done_marker_observed"],
                    "streamed_bytes": out["streamed_bytes"],
                    "stream_sha256": out["stream_sha256"],
                    "content_saved": False,
                }
            )
        audited_calls = len(audit.document["requests"])
        assert info["backend_calls"] == 1
        assert [x["http_status"] for x in result] == [200, 401, 401]
        assert result[0]["stream_done_marker_observed"]
        assert audited_calls == 3
        assert all(x["state"] == "FINISHED" for x in audit.document["requests"])
        assert [x["auth_verdict"] for x in audit.document["requests"]] == [
            "pass",
            "denied",
            "denied",
        ]
        audit.document["synthetic_results"] = result
        audit.document["real_model_backend_calls"] = info["backend_calls"]
        audit.document["ephemeral_sqlite_store_removed_on_exit"] = True
        digest = audit.close()
    return {
        "receipt_path": str(audit.path),
        "receipt_sha256": digest,
        "results": result,
        "backend_calls": info["backend_calls"],
        "cutover_permitted": False,
        "private_test_key_persisted": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(main()), sort_keys=True))
    except (
        TimeoutError,
        OSError,
        PermissionError,
        ValueError,
        LingReceiptDenied,
        RuntimeError,
    ) as exc:
        print(json.dumps({"status": "NO_GO", "error_class": type(exc).__name__}), file=sys.stderr)
        sys.exit(2)
