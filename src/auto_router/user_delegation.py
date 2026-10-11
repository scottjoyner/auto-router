"""Opt-in P0 #37 per-human delegation *admission* boundary.

Only a trusted gateway's separately signed, short-lived delegation can bind a
tenant/subject/root/attempt to an existing authenticated service request.
Absent delegations retain legacy behavior with UNKNOWN HUMAN ownership.
Disabled by default, and intentionally does not emit execution attestations,
grant trace readback, verify an IdP or alter configured providers.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.responses import JSONResponse

from auto_router.request_idempotency import _PROTECTED_PATHS
from auto_router.user_root_ledger import (
    RootOwnerConflict,
    RootOwnerLedger,
    RootOwnerStorageError,
    read_pinned_root_secret,
)

_HEADER = b"x-auto-router-user-delegation"
_SCOPE_KEY = "auto_router.p0_verified_user_delegation"
_TYP = "auto-router-user-delegation+jwt"
_AUDIENCE = "auto-router-p0-shadow-user"
_ISSUER = "trusted-user-gateway-p0"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_JWT_PART = re.compile(r"[A-Za-z0-9_-]+\Z")


class DelegationError(ValueError):
    """Fixed, sanitized errors only; never include tokens or prompt content."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DelegationError("duplicate delegation JSON field")
        result[key] = value
    return result


def _decode_part(part: str) -> bytes:
    if not _JWT_PART.fullmatch(part):
        raise DelegationError("invalid delegation encoding")
    try:
        decoded = base64.urlsafe_b64decode(part + "=" * ((4 - len(part) % 4) % 4))
    except (ValueError, TypeError, binascii.Error) as exc:
        raise DelegationError("invalid delegation encoding") from exc
    # Cryptographic verification authenticates bytes, not an arbitrary
    # non-canonical Base64URL spelling of those bytes. Reject unused-bit
    # aliases so one signature has exactly one accepted token encoding.
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != part:
        raise DelegationError("noncanonical delegation encoding")
    return decoded


def _decode_json(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DelegationError("invalid delegation JSON") from exc
    if not isinstance(value, dict):
        raise DelegationError("invalid delegation object")
    return value


def _uuid4(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def verify_user_delegation(
    token: str,
    *,
    public_key: Ed25519PublicKey,
    key_id: str,
    model: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Validate gateway signature AND exact model scope, no client metadata.

    Returns limited scalar fields suitable for an in-process ASGI scope.
    No statement about independent human login or actual model execution.
    """
    if not isinstance(token, str) or not 1 <= len(token) <= 3072:
        raise DelegationError("delegation length invalid")
    parts = token.split(".")
    if len(parts) != 3:
        raise DelegationError("invalid delegation segments")
    header = _decode_json(_decode_part(parts[0]))
    claims = _decode_json(_decode_part(parts[1]))
    if set(header) != {"alg", "typ", "kid"} or header != {
        "alg": "EdDSA", "typ": _TYP, "kid": key_id
    }:
        raise DelegationError("untrusted delegation header")
    try:
        signature = _decode_part(parts[2])
        if len(signature) != 64:
            raise DelegationError("invalid delegation signature")
        public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
    except DelegationError:
        raise
    except Exception as exc:
        raise DelegationError("invalid delegation signature") from exc

    required = {
        "schema", "iss", "aud", "tenant", "subject", "root_request_id",
        "attempt_id", "model_handle", "iat", "nbf", "exp", "jti",
    }
    if set(claims) != required:
        raise DelegationError("invalid delegation claims schema")
    if (
        claims["schema"] != "p0-user-shadow-v1"
        or claims["iss"] != _ISSUER
        or claims["aud"] != _AUDIENCE
    ):
        raise DelegationError("invalid delegation audience")
    for name in ("tenant", "subject", "jti"):
        if not isinstance(claims[name], str) or not _IDENTIFIER.fullmatch(claims[name]):
            raise DelegationError("invalid delegation principal")
    for name in ("root_request_id", "attempt_id"):
        if not _uuid4(claims[name]):
            raise DelegationError("invalid delegated attempt identity")
    if claims["root_request_id"] == claims["attempt_id"]:
        raise DelegationError("root and attempt identities must differ")
    bound_model = claims["model_handle"]
    if (
        not isinstance(bound_model, str)
        or not _MODEL.fullmatch(bound_model)
        or bound_model != model
    ):
        raise DelegationError("delegation model scope mismatch")
    issued, not_before, expires = (
        claims["iat"], claims["nbf"], claims["exp"]
    )
    if any(type(value) is not int for value in (issued, not_before, expires)):
        raise DelegationError("invalid delegation timestamps")
    current = int(time.time()) if now is None else now
    if (
        not 0 < expires - issued <= 120
        or not issued - 5 <= not_before <= issued + 5
        or issued > current + 5
        or not_before > current
        or expires <= current
    ):
        raise DelegationError("expired or premature user delegation")
    return {
        "issuer": _ISSUER,
        "tenant": claims["tenant"],
        "subject": claims["subject"],
        "root_request_id": claims["root_request_id"],
        "attempt_id": claims["attempt_id"],
        "model_handle": bound_model,
        "jti": claims["jti"],
    }


def _delegation_header(scope: dict[str, Any]) -> str | None:
    values = [
        value for key, value in scope.get("headers") or []
        if key.lower() == _HEADER
    ]
    if not values:
        return None
    if len(values) != 1 or len(values[0]) > 3072:
        raise DelegationError("ambiguous or oversized delegation header")
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError as exc:
        raise DelegationError("invalid delegation header encoding") from exc


def _service_bearer(scope: dict[str, Any], secret: str) -> bool:
    auth = [
        value for key, value in scope.get("headers") or []
        if key.lower() == b"authorization"
    ]
    if len(auth) != 1 or not secret:
        return False
    try:
        candidate = auth[0].decode("ascii")
    except UnicodeDecodeError:
        return False
    if not candidate.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(candidate[7:].strip(), secret)


async def _bounded_request_body(receive: Any, *, max_bytes: int = 262144):
    """Bound memory before parsing; retain client-disconnect propagation."""
    body = bytearray()
    more = True
    while more:
        message = await receive()
        if message.get("type") != "http.request":
            raise DelegationError("invalid delegated request body")
        chunk = message.get("body", b"")
        if not isinstance(chunk, bytes) or len(body) + len(chunk) > max_bytes:
            raise DelegationError("delegated request too large")
        body.extend(chunk)
        more = bool(message.get("more_body", False))
    replayed = False

    async def replay():
        nonlocal replayed
        if replayed:
            return await receive()
        replayed = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return bytes(body), replay


class UserDelegationShadowMiddleware:
    """Runs after existing executor auth, before request idempotency.

    It only adds a verified in-process ASGI scope; no untrusted body metadata
    is ever treated as a verified human. Missing token is legacy/UNKNOWN.
    """

    def __init__(
        self, app: Any, *, public_key: Ed25519PublicKey, key_id: str,
        owner_ledger: RootOwnerLedger,
    ) -> None:
        self.app = app
        self.public_key = public_key
        self.key_id = key_id
        self.owner_ledger = owner_ledger

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "").upper() != "POST"
            or scope.get("path") not in _PROTECTED_PATHS
        ):
            await self.app(scope, receive, send)
            return
        if os.getenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true").lower() not in {
            "1", "true", "yes", "on"
        }:
            await JSONResponse(
                status_code=503, content={"detail": "executor auth must remain enabled"}
            )(scope, receive, send)
            return
        try:
            delegation = _delegation_header(scope)
            if delegation is None:
                await self.app(scope, receive, send)
                return
            service_secret = os.getenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "").strip()
            if not _service_bearer(scope, service_secret):
                raise DelegationError("delegation requires authenticated gateway service")
            data, replay = await _bounded_request_body(receive)
            body = _decode_json(data)
            model = body.get("model")
            if not isinstance(model, str):
                raise DelegationError("delegated model is required")
            bound = verify_user_delegation(
                delegation,
                public_key=self.public_key,
                key_id=self.key_id,
                model=model,
            )
            # Persist the unique owner/model/root/attempt binding BEFORE any
            # downstream idempotency reservation or routing. A repeated
            # signed attempt never forwards again, even after a restart/TTL.
            created = self.owner_ledger.reserve(bound, delegation)
            if not created:
                raise RootOwnerConflict("duplicate delegated attempt")
            # This is trusted in-process verifier scope, not caller metadata.
            # It is NOT provider-execution or response-delivery evidence.
            scope[_SCOPE_KEY] = bound
            await self.app(scope, replay, send)
        except RootOwnerConflict:
            await JSONResponse(
                status_code=409, content={"detail": "delegated root/attempt conflict"}
            )(scope, receive, send)
        except RootOwnerStorageError:
            await JSONResponse(
                status_code=503, content={"detail": "durable owner ledger unavailable"}
            )(scope, receive, send)
        except DelegationError as exc:
            await JSONResponse(status_code=401, content={"detail": str(exc)})(
                scope, receive, send
            )


def install_user_delegation_shadow(app: Any) -> None:
    """Explicit opt-in, fail closed for missing/misconfigured trusted key."""
    if getattr(app.state, "p0_user_delegation_shadow_installed", False):
        return
    enabled = os.getenv("AUTO_ROUTER_P0_USER_DELEGATION_SHADOW_ENABLED", "").lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    if os.getenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true").lower() not in {
        "1", "true", "yes", "on"
    }:
        raise RuntimeError("P0 shadow delegation requires executor authentication")
    if not os.getenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "").strip():
        raise RuntimeError("P0 shadow delegation requires an authenticated gateway service")
    key_file = os.getenv("AUTO_ROUTER_P0_USER_DELEGATION_VERIFY_KEY_FILE", "")
    key_id = os.getenv("AUTO_ROUTER_P0_USER_DELEGATION_KEY_ID", "")
    if not key_file.startswith("/") or not key_id or not _IDENTIFIER.fullmatch(key_id):
        raise RuntimeError("P0 shadow delegation requires pinned absolute key path and key id")
    try:
        public_key = serialization.load_pem_public_key(Path(key_file).read_bytes())
    except (ValueError, OSError, TypeError) as exc:
        raise RuntimeError("invalid P0 shadow delegation verification key") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise RuntimeError("P0 delegation verification key must be Ed25519")
    ledger_file = os.getenv("AUTO_ROUTER_P0_ROOT_OWNER_LEDGER_FILE", "")
    secret_file = os.getenv("AUTO_ROUTER_P0_ROOT_OWNER_HMAC_KEY_FILE", "")
    if not ledger_file.startswith("/") or not secret_file.startswith("/"):
        raise RuntimeError("P0 shadow requires separate pinned durable owner ledger and key")
    try:
        owner_ledger = RootOwnerLedger(
            Path(ledger_file), read_pinned_root_secret(secret_file)
        )
    except RootOwnerStorageError as exc:
        raise RuntimeError("P0 durable owner ledger not ready") from exc
    app.add_middleware(
        UserDelegationShadowMiddleware,
        public_key=public_key, key_id=key_id,
        owner_ledger=owner_ledger,
    )
    app.state.p0_user_delegation_shadow_installed = True


__all__ = [
    "DelegationError", "UserDelegationShadowMiddleware",
    "verify_user_delegation", "install_user_delegation_shadow",
]
