"""Not deployed: narrowly scoped Ling JWT verification and durable one-shot receipts.

This is an isolated contract, NOT a running reverse proxy or an authorization
to admit a fleet provider. It requires independently trusted caller identity,
a pinned running-model digest and a current projection generation supplied by
a future authenticated ingress, none of which this module discovers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_AUDIENCE = "joyner-ling"
_TYPE = "assistx-joyner-ling+jwt"
_ISSUER = "assistx"
_KID = "joyner-ling-v1"
_ALIAS = "joyner-ling"
_SCOPE = "inference:ling"
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_:.@-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_BASE64 = re.compile(r"[A-Za-z0-9_-]+\Z")
_MAX_BODY = 64 * 1024
_MAX_TOKEN = 8 * 1024


class LingReceiptDenied(ValueError):
    """Does not include credential bytes, prompts, public-key material or user text."""


@dataclass(frozen=True)
class LingReceipt:
    principal_id: str
    task_id: str
    claim_id: str
    agent_id: str
    jti: str
    projection_generation: int
    model_sha256: str
    request_sha256: str
    verified_signature: bool = True
    replay_reserved: bool = True
    # Verification never grants routing, training, or service deployment.
    approved_for_proxy_cutover: bool = False
    approved_for_training: bool = False


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, val in pairs:
        if key in result:
            raise LingReceiptDenied("duplicate_json_field")
        result[key] = val
    return result


def _json_object(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise LingReceiptDenied(reason) from exc
    if not isinstance(obj, dict):
        raise LingReceiptDenied(reason)
    return obj


def _decode(part: str) -> bytes:
    if not _BASE64.fullmatch(part) or len(part) > _MAX_TOKEN:
        raise LingReceiptDenied("invalid_token_encoding")
    try:
        return base64.b64decode(part + "=" * ((-len(part)) % 4), altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise LingReceiptDenied("invalid_token_encoding") from exc


def _identity(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LingReceiptDenied(reason)
    return value


def _uint(value: Any, reason: str) -> int:
    if type(value) is not int or value < 1:
        raise LingReceiptDenied(reason)
    return value


def verify_signature_and_scope(
    token: str,
    request_body: bytes,
    *,
    public_key: Ed25519PublicKey,
    trusted_caller_principal: str,
    trusted_projection_generation: int,
    trusted_running_model_sha256: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify a distinct-audience bearer bound to *exact* HTTP body bytes.

    trusted_* arguments must come from independently authenticated, fenced
    sources, not from JSON, request headers or an ordinary model catalog.
    """
    if not isinstance(token, str) or len(token) > _MAX_TOKEN:
        raise LingReceiptDenied("invalid_ling_token")
    if not isinstance(request_body, bytes) or not 0 < len(request_body) <= _MAX_BODY:
        raise LingReceiptDenied("request_body_size_rejected")
    if not isinstance(public_key, Ed25519PublicKey):
        raise LingReceiptDenied("trusted_ed25519_key_missing")
    _identity(trusted_caller_principal, "trusted_caller_missing")
    generation = _uint(trusted_projection_generation, "trusted_projection_unavailable")
    if not isinstance(trusted_running_model_sha256, str) or not _SHA.fullmatch(
        trusted_running_model_sha256
    ):
        raise LingReceiptDenied("trusted_running_model_digest_missing")

    parts = token.split(".")
    if len(parts) != 3:
        raise LingReceiptDenied("invalid_ling_token")
    encoded_header, encoded_claims, encoded_signature = parts
    header = _json_object(_decode(encoded_header), "invalid_ling_header")
    claims = _json_object(_decode(encoded_claims), "invalid_ling_claims")
    if header != {"alg": "EdDSA", "typ": _TYPE, "kid": _KID}:
        raise LingReceiptDenied("ling_token_domain_mismatch")
    try:
        public_key.verify(
            _decode(encoded_signature), f"{encoded_header}.{encoded_claims}".encode("ascii")
        )
    except Exception as exc:
        raise LingReceiptDenied("invalid_ling_signature") from exc

    current = int(now if now is not None else time.time())
    issued = _uint(claims.get("iat"), "invalid_ling_time")
    not_before = _uint(claims.get("nbf"), "invalid_ling_time")
    expires = _uint(claims.get("exp"), "invalid_ling_time")
    if (
        issued > current + 5
        or not_before > current + 5
        or not_before < issued - 5
        or expires <= current
        or expires <= issued
        or expires - issued > 120
    ):
        raise LingReceiptDenied("ling_token_expired_or_outside_window")
    if (
        claims.get("iss") != _ISSUER
        or claims.get("aud") != _AUDIENCE
        or claims.get("scopes") != [_SCOPE]
        or claims.get("model_alias") != _ALIAS
        or type(claims.get("max_attempts")) is not int
        or claims.get("max_attempts") != 1
    ):
        raise LingReceiptDenied("ling_token_scope_mismatch")
    for field in ("principal_id", "task_id", "claim_id", "agent_id", "jti"):
        _identity(claims.get(field), "missing_or_invalid_ling_identity")
    if claims["principal_id"] != trusted_caller_principal:
        raise LingReceiptDenied("caller_principal_mismatch")
    if _uint(claims.get("projection_generation"), "invalid_projection_generation") != generation:
        raise LingReceiptDenied("stale_or_incorrect_projection_generation")
    if claims.get("model_sha256") != trusted_running_model_sha256:
        raise LingReceiptDenied("running_model_digest_mismatch")
    if (
        not isinstance(claims.get("request_sha256"), str)
        or claims["request_sha256"] != hashlib.sha256(request_body).hexdigest()
    ):
        raise LingReceiptDenied("signed_request_bytes_mismatch")

    payload = _json_object(request_body, "invalid_ling_request_json")
    if payload.get("model") != _ALIAS or payload.get("stream") not in (True, False):
        raise LingReceiptDenied("ling_request_model_or_stream_invalid")
    if not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise LingReceiptDenied("ling_messages_missing")
    if len(payload["messages"]) > 64:
        raise LingReceiptDenied("ling_message_count_exceeded")
    max_output = payload.get("max_tokens")
    if type(max_output) is not int or not 1 <= max_output <= 512:
        raise LingReceiptDenied("ling_output_budget_invalid")
    if max_output > _uint(claims.get("max_output_tokens"), "signed_output_budget_invalid"):
        raise LingReceiptDenied("signed_output_budget_exceeded")
    return claims


def _reserve_one_use(
    claims: dict[str, Any], request_body: bytes,
    *, database_path: Path, now: int | None = None
) -> LingReceipt:
    """SQLite single-writer reservation survives process restarts and races.

    DB must be on independently isolated persistent storage with a qualified
    owner, not NAS, an ephemeral temp path, or a model-worker volume.
    """
    if not isinstance(database_path, Path) or not database_path.is_absolute():
        raise LingReceiptDenied("durable_receipt_path_missing")
    if not database_path.parent.is_dir() or database_path.is_symlink():
        raise LingReceiptDenied("durable_receipt_directory_unavailable")
    try:
        parent_mode = database_path.parent.stat().st_mode
        if parent_mode & 0o077:
            raise LingReceiptDenied("durable_receipt_directory_not_private")
        if database_path.exists():
            db_mode = database_path.lstat().st_mode
            if not stat.S_ISREG(db_mode) or db_mode & 0o077:
                raise LingReceiptDenied("durable_receipt_database_not_private")
        else:
            # Create with restrictive permissions before SQLite first opens it,
            # instead of relying on a process-global umask that can affect peers.
            fd = os.open(str(database_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
    except OSError as exc:
        raise LingReceiptDenied("durable_receipt_file_unavailable") from exc
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(database_path), timeout=1.5, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=1500")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS ling_receipt ("
            "jti TEXT PRIMARY KEY, principal_id TEXT NOT NULL, task_id TEXT NOT NULL,"
            "claim_id TEXT NOT NULL, projection_generation INTEGER NOT NULL,"
            "expires INTEGER NOT NULL, request_sha256 TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS ling_highwater ("
            "principal_id TEXT PRIMARY KEY, projection_generation INTEGER NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT projection_generation FROM ling_highwater WHERE principal_id=?",
            (claims["principal_id"],),
        ).fetchone()
        if prior is not None and int(prior[0]) > int(claims["projection_generation"]):
            raise LingReceiptDenied("projection_generation_regressed")
        connection.execute(
            "INSERT INTO ling_receipt "
            "(jti, principal_id, task_id, claim_id, projection_generation, expires, request_sha256)"
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (claims["jti"], claims["principal_id"], claims["task_id"], claims["claim_id"],
             claims["projection_generation"], claims["exp"],
             hashlib.sha256(request_body).hexdigest()),
        )
        connection.execute(
            "INSERT INTO ling_highwater (principal_id, projection_generation) "
            "VALUES (?, ?) ON CONFLICT(principal_id) DO UPDATE SET "
            "projection_generation=MAX(projection_generation,excluded.projection_generation)",
            (claims["principal_id"], claims["projection_generation"]),
        )
        connection.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        if connection is not None and connection.in_transaction:
            connection.execute("ROLLBACK")
        raise LingReceiptDenied("ling_receipt_replayed") from exc
    except sqlite3.Error as exc:
        if connection is not None and connection.in_transaction:
            connection.execute("ROLLBACK")
        raise LingReceiptDenied("durable_receipt_store_unavailable") from exc
    except LingReceiptDenied:
        if connection is not None and connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        if connection is not None:
            connection.close()
    return LingReceipt(
        principal_id=claims["principal_id"], task_id=claims["task_id"],
        claim_id=claims["claim_id"], agent_id=claims["agent_id"],
        jti=claims["jti"], projection_generation=claims["projection_generation"],
        model_sha256=claims["model_sha256"],
        request_sha256=hashlib.sha256(request_body).hexdigest(),
    )


def validate_and_reserve(
    token: str, request_body: bytes, *,
    public_key: Ed25519PublicKey, trusted_caller_principal: str,
    trusted_projection_generation: int, trusted_running_model_sha256: str,
    database_path: Path, now: int | None = None,
) -> LingReceipt:
    claims = verify_signature_and_scope(
        token, request_body, public_key=public_key,
        trusted_caller_principal=trusted_caller_principal,
        trusted_projection_generation=trusted_projection_generation,
        trusted_running_model_sha256=trusted_running_model_sha256, now=now,
    )
    return _reserve_one_use(claims, request_body, database_path=database_path, now=now)
