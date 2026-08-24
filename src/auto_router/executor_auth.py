from __future__ import annotations

import base64
import hmac
import json
import logging
logger = logging.getLogger(__name__)
import math
import os
import threading
import time
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.responses import JSONResponse

_PROTECTED_PATHS = {
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/completions",
    "/v1/embeddings",
}
_CONTROL_FIELDS = {
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "n",
    "parallel_tool_calls",
    "presence_penalty",
    "seed",
    "service_tier",
    "stop",
    "stream",
    "stream_options",
    "temperature",
    "timeout",
    "top_logprobs",
    "top_p",
    "user",
}


class ExecutorAuthError(ValueError):
    pass


def _b64decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ExecutorAuthError("executor token contains invalid base64") from exc


def _read_key() -> bytes:
    path = os.getenv("AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE", "").strip()
    if path:
        with open(path, "rb") as handle:
            return handle.read()
    value = os.getenv("AUTO_ROUTER_EXECUTOR_VERIFY_KEY_PEM", "")
    return value.replace("\\n", "\n").encode("utf-8") if value else b""


def _load_public_key() -> Ed25519PublicKey:
    raw = _read_key()
    if not raw:
        raise ExecutorAuthError("auto-router executor verification key is not configured")
    try:
        key = serialization.load_pem_public_key(raw)
    except ValueError:
        try:
            key = Ed25519PublicKey.from_public_bytes(_b64decode(raw.decode("ascii")))
        except Exception as exc:
            raise ExecutorAuthError("auto-router executor verification key is invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ExecutorAuthError("auto-router executor verification key must be Ed25519")
    return key


def decode_executor_token(token: str, *, now: int | None = None) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise ExecutorAuthError("executor token must contain three segments")
    encoded_header, encoded_claims, encoded_signature = parts
    try:
        header = json.loads(_b64decode(encoded_header))
        claims = json.loads(_b64decode(encoded_claims))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutorAuthError("executor token JSON is invalid") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise ExecutorAuthError("executor token payload is invalid")
    expected_key_id = os.getenv("AUTO_ROUTER_EXECUTOR_KEY_ID", "assistx-executor-v1")
    if (
        header.get("alg") != "EdDSA"
        or header.get("typ") != "assistx-executor+jwt"
        or str(header.get("kid") or "") != expected_key_id
    ):
        raise ExecutorAuthError("executor token header is not accepted")
    try:
        _load_public_key().verify(
            _b64decode(encoded_signature),
            f"{encoded_header}.{encoded_claims}".encode("ascii"),
        )
    except Exception as exc:
        raise ExecutorAuthError("executor token signature is invalid") from exc

    current = int(now if now is not None else time.time())
    issued = int(claims.get("iat") or 0)
    not_before = int(claims.get("nbf") or issued)
    expires = int(claims.get("exp") or 0)
    if not issued or issued > current + 30:
        raise ExecutorAuthError("executor token issuance time is invalid")
    if not_before > current + 5:
        raise ExecutorAuthError("executor token is not active")
    if expires <= current:
        raise ExecutorAuthError("executor token is expired")
    audiences = claims.get("aud") or []
    if isinstance(audiences, str):
        audiences = [audiences]
    if "auto-router" not in audiences or claims.get("iss") != "assistx":
        raise ExecutorAuthError("executor token issuer or audience is not accepted")
    if "inference" not in {str(item) for item in claims.get("scopes") or []}:
        raise ExecutorAuthError("executor token lacks inference scope")
    for name in ("task_id", "claim_id", "agent_id", "jti", "projection_generation"):
        if not str(claims.get(name) or "").strip():
            raise ExecutorAuthError(f"executor token is missing {name}")
    return claims


def _bearer(headers: list[tuple[bytes, bytes]]) -> str:
    for key, value in headers:
        if key.lower() == b"authorization":
            text = value.decode("latin-1").strip()
            if text.lower().startswith("bearer "):
                return text[7:].strip()
    return ""


async def _read_body(receive: Any) -> tuple[bytes, Any]:
    body = bytearray()
    more = True
    while more:
        message = await receive()
        body.extend(message.get("body", b""))
        more = bool(message.get("more_body", False))
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return bytes(body), replay


def _estimate_tokens(payload: Mapping[str, Any]) -> int:
    context = {
        str(key): value
        for key, value in payload.items()
        if str(key).lower() not in _CONTROL_FIELDS
    }
    try:
        text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(context)
    return max(1, math.ceil(len(text) / 3.5))


def _max_output(payload: Mapping[str, Any]) -> int:
    value = payload.get("max_completion_tokens") or payload.get("max_tokens") or 4096
    try:
        return max(1, int(value))
    except (TypeError, ValueError) as exc:
        raise ExecutorAuthError("requested output token limit is invalid") from exc


def _encoded_receive(payload: Mapping[str, Any]) -> Any:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    return receive


class _AttemptLedger:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, tuple[int, int]] = {}

    def acquire(self, jti: str, expires: int, maximum: int) -> int:
        now = int(time.time())
        with self._lock:
            self._records = {
                key: value for key, value in self._records.items() if value[1] > now
            }
            count, _ = self._records.get(jti, (0, expires))
            if count >= maximum:
                raise ExecutorAuthError("executor token inference-attempt budget is exhausted")
            count += 1
            self._records[jti] = (count, expires)
            return count


_ATTEMPTS = _AttemptLedger()


class ExecutorInferenceAuthMiddleware:
    def __init__(self, app: Any, state: Any) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "GET").upper() != "POST"
            or str(scope.get("path") or "") not in _PROTECTED_PATHS
        ):
            await self.app(scope, receive, send)
            return
        if os.getenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            await self.app(scope, receive, send)
            return

        body, replay = await _read_body(receive)
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ExecutorAuthError("inference request must be a JSON object")
            bearer = _bearer(list(scope.get("headers") or []))
            service_token = os.getenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "").strip()
            if service_token and hmac.compare_digest(bearer, service_token):
                metadata = payload.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                payload["metadata"] = {
                    **metadata,
                    "assistx_service": {
                        "identity": "assistx-internal",
                        "authenticated": True,
                    },
                }
                await self.app(scope, _encoded_receive(payload), send)
                return

            claims = decode_executor_token(bearer)
            model = str(payload.get("model") or "").strip()
            allowed_models = {str(item) for item in claims.get("allowed_model_aliases") or []}
            if not model or model not in allowed_models:
                logger.warning(
                    "executor scope reject: requested=%r allowed=%r gen=%r",
                    model,
                    sorted(allowed_models),
                    claims.get("projection_generation"),
                )
                raise ExecutorAuthError("requested model is outside the executor token scope")
            input_tokens = _estimate_tokens(payload)
            output_tokens = _max_output(payload)
            if input_tokens > int(claims.get("max_input_tokens") or 0):
                raise ExecutorAuthError("inference input exceeds executor token budget")
            if output_tokens > int(claims.get("max_output_tokens") or 0):
                raise ExecutorAuthError("inference output exceeds executor token budget")

            manager = getattr(self.state, "runtime_projection_manager", None)
            current = getattr(manager, "current", None)
            if current is None or not current.is_fresh():
                raise ExecutorAuthError("current AssistX runtime projection is absent or expired")
            token_generation = int(claims.get("projection_generation") or 0)
            if token_generation != int(current.generation):
                raise ExecutorAuthError("executor token runtime projection generation is stale")
            attempt = _ATTEMPTS.acquire(
                str(claims["jti"]),
                int(claims["exp"]),
                max(1, int(claims.get("max_attempts") or 1)),
            )
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            payload["metadata"] = {
                **metadata,
                "assistx_executor": {
                    "task_id": claims["task_id"],
                    "claim_id": claims["claim_id"],
                    "agent_id": claims["agent_id"],
                    "token_jti": claims["jti"],
                    "attempt": attempt,
                    "projection_generation": token_generation,
                },
            }
            await self.app(scope, _encoded_receive(payload), send)
        except (ExecutorAuthError, json.JSONDecodeError) as exc:
            await JSONResponse(status_code=401, content={"detail": str(exc)})(scope, replay, send)


def install_executor_inference_auth(app: Any, state: Any) -> None:
    if getattr(app.state, "executor_inference_auth_installed", False):
        return
    app.add_middleware(ExecutorInferenceAuthMiddleware, state=state)
    app.state.executor_inference_auth_installed = True
