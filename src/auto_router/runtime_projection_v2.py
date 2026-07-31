from __future__ import annotations

import base64
import hmac
import json
import os
from pathlib import Path
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, Field

from auto_router import runtime_projection as legacy
from auto_router.models import ProviderConfig


_ALGORITHM = "Ed25519"
_DEFAULT_KEY_ID = "assistx-runtime-projection-v1"
_INTERNAL_COMPAT_SECRET = "auto-router-ed25519-projection-wrapper"


class RuntimeProjectionDocument(BaseModel):
    schema_version: Literal["2"] = "2"
    source: Literal["assistx"] = "assistx"
    generation: int = Field(ge=1)
    revision: str = Field(min_length=1, max_length=300)
    generated_at_ms: int = Field(gt=0)
    expires_at_ms: int = Field(gt=0)
    providers: list[ProviderConfig] = Field(min_length=1)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_algorithm: Literal["Ed25519"] = "Ed25519"
    signature_key_id: str = Field(min_length=1, max_length=200)
    signature: str = Field(min_length=80, max_length=100)


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ValueError("runtime projection signature is not valid base64url") from exc


def _public_key_bytes_from_env() -> bytes:
    path_value = os.getenv("AUTO_ROUTER_RUNTIME_PROJECTION_VERIFY_KEY_FILE", "").strip()
    if path_value:
        return Path(path_value).read_bytes()
    value = os.getenv("AUTO_ROUTER_RUNTIME_PROJECTION_VERIFY_KEY_PEM", "")
    return value.replace("\\n", "\n").encode("utf-8") if value else b""


def load_public_key() -> Ed25519PublicKey:
    raw = _public_key_bytes_from_env()
    if not raw:
        raise ValueError("runtime projection Ed25519 verification key is required")
    try:
        key = serialization.load_pem_public_key(raw)
    except ValueError:
        try:
            key = Ed25519PublicKey.from_public_bytes(_b64url_decode(raw.decode("ascii")))
        except Exception as exc:
            raise ValueError("runtime projection verification key is invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("runtime projection verification key must be Ed25519")
    return key


def signing_message(document: dict[str, Any]) -> bytes:
    payload = {
        "schema_version": str(document.get("schema_version") or ""),
        "source": str(document.get("source") or ""),
        "generation": int(document.get("generation") or 0),
        "revision": str(document.get("revision") or ""),
        "checksum": str(document.get("checksum") or ""),
        "generated_at_ms": int(document.get("generated_at_ms") or 0),
        "expires_at_ms": int(document.get("expires_at_ms") or 0),
        "signature_algorithm": str(document.get("signature_algorithm") or ""),
        "signature_key_id": str(document.get("signature_key_id") or ""),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _legacy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    converted = {
        key: value
        for key, value in payload.items()
        if key not in {"signature_algorithm", "signature_key_id"}
    }
    converted["schema_version"] = "1"
    converted["checksum"] = legacy.projection_checksum(converted)
    converted["signature"] = legacy.projection_signature(
        int(converted["generation"]),
        str(converted["checksum"]),
        int(converted["generated_at_ms"]),
        int(converted["expires_at_ms"]),
        _INTERNAL_COMPAT_SECRET,
    )
    return converted


def validate_projection_document(
    payload: dict[str, Any],
    *,
    now_ms: int | None = None,
    public_key: Ed25519PublicKey | None = None,
) -> tuple[RuntimeProjectionDocument, dict[str, Any]]:
    if not legacy.strict_offline_enabled():
        raise ValueError("runtime projection is supported only in strict offline mode")
    if not isinstance(payload, dict):
        raise ValueError("runtime projection must be a JSON object")

    document = RuntimeProjectionDocument.model_validate(payload)
    expected_key_id = os.getenv(
        "AUTO_ROUTER_RUNTIME_PROJECTION_KEY_ID",
        _DEFAULT_KEY_ID,
    ).strip() or _DEFAULT_KEY_ID
    if document.signature_key_id != expected_key_id:
        raise ValueError("runtime projection signing key id is not accepted")
    if document.signature_algorithm != _ALGORITHM:
        raise ValueError("runtime projection signature algorithm is not accepted")

    expected_checksum = legacy.projection_checksum(payload)
    if not hmac.compare_digest(document.checksum, expected_checksum):
        raise ValueError("runtime projection checksum mismatch")
    verifier = public_key or load_public_key()
    try:
        verifier.verify(
            _b64url_decode(document.signature),
            signing_message(payload),
        )
    except Exception as exc:
        raise ValueError("runtime projection Ed25519 signature mismatch") from exc

    converted = _legacy_payload(payload)
    legacy.validate_projection_document(
        converted,
        secret=_INTERNAL_COMPAT_SECRET,
        now_ms=now_ms,
    )
    return document, converted


class RuntimeProjectionManager(legacy.RuntimeProjectionManager):
    """Apply schema-v2 Ed25519 projections while retaining proven swap mechanics."""

    def _secret(self) -> str:
        # Used only by the internal schema-v1 compatibility payload after the
        # external Ed25519 signature has already been verified.
        return _INTERNAL_COMPAT_SECRET

    async def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_attempt_at_ms = int(legacy.time.time() * 1000)
        document, converted = validate_projection_document(payload)

        async with self._lock:
            if self.current is not None:
                if document.generation < self.current.generation:
                    raise ValueError("runtime projection generation rollback is forbidden")
                if document.generation == self.current.generation:
                    if document.checksum != self.current.checksum:
                        raise ValueError("runtime projection generation checksum conflict")
                    self.current.generated_at_ms = document.generated_at_ms
                    self.current.expires_at_ms = document.expires_at_ms
                    self.current.revision = document.revision
                    self.last_error = ""
                    return {
                        "applied": False,
                        "idempotent": True,
                        "lease_refreshed": True,
                        **self.status(),
                    }
                if document.generation != self.current.generation + 1:
                    raise ValueError(
                        "runtime projection generation must advance exactly by one"
                    )

        result = await super().apply(converted)
        if self.current is not None and self.current.generation == document.generation:
            self.current.checksum = document.checksum
            self.current.revision = document.revision
            self.current.generated_at_ms = document.generated_at_ms
            self.current.expires_at_ms = document.expires_at_ms
        return {
            **result,
            **self.status(),
        }


projection_poll_task = legacy.projection_poll_task
