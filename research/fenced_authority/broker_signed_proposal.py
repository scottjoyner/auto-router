"""Offline two-trust-root broker proposal validator. NOT an execution permit.

Validates an executor JWT (one pinned Ed25519 key) and an independent
current-resource witness (a DIFFERENT pinned key) before constructing
hash-only arguments for the disposable fence SQL. Does not connect to any
database, grant authority, contact a network or reserve provider quota.

A previously signed "active" witness may still be replayed after revocation
without a durable independent high-water or resource-held live revocation
check. Therefore the resulting proposal is NEVER runnable by this module.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

class ResearchDeny(ValueError):
    """Fail closed for missing, forged, stale, or incongruent claims."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def b64url(value: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise ResearchDeny("noncanonical signature or encoded claim")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4),
                               altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ResearchDeny("invalid base64url encoding") from exc
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise ResearchDeny("noncanonical base64url encoding")
    return raw


def sha(domain: str, text: str) -> str:
    return hashlib.sha256(canonical([domain, text])).hexdigest()


def owner_identity_hash(issuer: str, subject: str) -> str:
    """Hash two *separately framed* signed fields, not a separator-joined string.

    Concatenating with "\\x00" lets ('idp\\x00a', 'b') alias ('idp', 'a\\x00b')
    even though both owner assertions are different and correctly signed.
    Use an explicitly versioned tuple so embedded delimiters cannot collide.
    """
    return hashlib.sha256(
        canonical(["owner-issuer-subject-v2", issuer, subject])
    ).hexdigest()


def only_dict(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ResearchDeny("invalid signed JSON") from exc
    if not isinstance(value, dict):
        raise ResearchDeny("signed claim must be an object")
    if canonical(value) != raw:
        raise ResearchDeny("signed JSON is not canonical")
    return value


def _key(key: Ed25519PublicKey) -> bytes:
    if not isinstance(key, Ed25519PublicKey):
        raise ResearchDeny("pinned Ed25519 public key required")
    return key.public_bytes(encoding=serialization.Encoding.Raw,
                            format=serialization.PublicFormat.Raw)


def _signed_jwt(token: str, pinned: Ed25519PublicKey) -> dict[str, Any]:
    if not isinstance(token, str) or len(token) > 8192:
        raise ResearchDeny("invalid executor token size")
    parts = token.split(".")
    if len(parts) != 3:
        raise ResearchDeny("signed executor JWT must have three segments")
    header = only_dict(b64url(parts[0]))
    if header != {"alg": "EdDSA", "kid": "assistx-executor-v1",
                  "typ": "assistx-executor+jwt"}:
        raise ResearchDeny("untrusted JWT header")
    claims = only_dict(b64url(parts[1]))
    signature = b64url(parts[2])
    if len(signature) != 64:
        raise ResearchDeny("invalid executor signature length")
    try:
        pinned.verify(signature, (parts[0] + "." + parts[1]).encode("ascii"))
    except InvalidSignature as exc:
        raise ResearchDeny("executor signature not from pinned issuer") from exc
    return claims


def _signed_resource_witness(
    witness: Any, pinned: Ed25519PublicKey,
) -> dict[str, Any]:
    if not isinstance(witness, Mapping) or set(witness) != {
        "payload", "signature", "signer_id"
    }:
        raise ResearchDeny("resource witness envelope invalid")
    if witness["signer_id"] != "resource-witness-v1":
        raise ResearchDeny("untrusted resource signer id")
    payload = witness["payload"]
    if not isinstance(payload, dict):
        raise ResearchDeny("resource payload invalid")
    data = canonical(payload)
    if len(data) > 4096:
        raise ResearchDeny("resource witness too large")
    sig = b64url(witness["signature"])
    if len(sig) != 64:
        raise ResearchDeny("resource signature length invalid")
    try:
        pinned.verify(sig, data)
    except InvalidSignature as exc:
        raise ResearchDeny("resource witness signature not from pinned root") from exc
    return payload


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ResearchDeny("invalid " + name)
    return value


def _positive(value: Any, name: str) -> int:
    # Prevent bool-as-int, floats, strings, and forged arithmetic casts.
    if type(value) is not int or value <= 0 or value >= 2**63:
        raise ResearchDeny("invalid " + name)
    return value


def propose_only(
    *,
    token: str,
    resource_witness: Mapping[str, Any],
    issuer_public_key: Ed25519PublicKey,
    resource_public_key: Ed25519PublicKey,
    requested_model: str,
    account_scope: str,
    request_id: str,
    now: int,
) -> dict[str, Any]:
    """Return NON-EXECUTABLE research hashes, never a grant or SQL command."""
    if _key(issuer_public_key) == _key(resource_public_key):
        raise ResearchDeny("issuer and resource must have independent trust roots")
    now = _positive(now, "clock")
    requested_model = _text(requested_model, "model")
    account_scope = _text(account_scope, "account")
    request_id = _text(request_id, "request")
    claims = _signed_jwt(token, issuer_public_key)
    required = ("task_id", "claim_id", "agent_id", "jti",
                "owner_issuer", "owner_subject")
    for name in required:
        _text(claims.get(name), name)
    if claims.get("iss") != "assistx" or claims.get("aud") != ["auto-router"]:
        raise ResearchDeny("untrusted executor issuer or audience")
    if claims.get("scopes") != ["inference"]:
        raise ResearchDeny("incorrect executor scopes")
    model_aliases = claims.get("allowed_model_aliases")
    if not isinstance(model_aliases, list) or not model_aliases or (
        len(model_aliases) != len(set(map(str, model_aliases)))
    ) or not all(isinstance(m, str) and m for m in model_aliases):
        raise ResearchDeny("invalid scoped model list")
    if requested_model not in model_aliases:
        raise ResearchDeny("requested model outside signed scope")
    iat = _positive(claims.get("iat"), "iat")
    nbf = _positive(claims.get("nbf"), "nbf")
    exp = _positive(claims.get("exp"), "exp")
    if iat > now or nbf > now or exp <= now or exp - iat > 60:
        raise ResearchDeny("executor token not current or too long-lived")
    generation = _positive(claims.get("projection_generation"), "generation")
    budget = _positive(claims.get("max_attempts"), "attempt budget")
    if budget > 100:
        raise ResearchDeny("attempt budget too high")
    for field in ("max_input_tokens", "max_output_tokens"):
        _positive(claims.get(field), field)

    witness = _signed_resource_witness(resource_witness, resource_public_key)
    expected = {
        "issuer": "resource-witness",
        "aud": "fence-research-only",
        "state": "active",
        "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "task_id": claims["task_id"],
        "claim_id": claims["claim_id"],
        "owner_issuer": claims["owner_issuer"],
        "owner_subject": claims["owner_subject"],
        "jti": claims["jti"],
        "model": requested_model,
        "account_scope": account_scope,
        "projection_generation": generation,
        "max_attempts": budget,
        "request_id": request_id,
    }
    for field, value in expected.items():
        if witness.get(field) != value:
            raise ResearchDeny("resource witness disagrees on " + field)
    if set(witness) != set(expected) | {
        "epoch", "issued_at", "expires_at", "revocation_generation",
        "runtime_instance_id"
    }:
        raise ResearchDeny("resource witness schema drift")
    _positive(witness.get("epoch"), "epoch")
    _positive(witness.get("revocation_generation"), "revocation generation")
    runtime = _text(witness.get("runtime_instance_id"), "runtime")
    start = _positive(witness.get("issued_at"), "resource issuance")
    end = _positive(witness.get("expires_at"), "resource expiry")
    # A witness is a time-limited statement, NOT fresh authoritative state.
    if start > now or end <= now or now - start > 5 or end - start > 20:
        raise ResearchDeny("stale or expired resource witness")
    if end > exp:
        raise ResearchDeny("resource witness outlives executor grant")

    scope_hash = sha("scope-v1", account_scope)
    return {
        "decision": "VERIFIED_SIGNED_INPUTS_ONLY_NOT_RESOURCE_ADMISSION",
        "scope_hash": scope_hash,
        "owner_hash": owner_identity_hash(claims["owner_issuer"], claims["owner_subject"]),
        "claim_hash": sha("claim-v1", claims["claim_id"]),
        "jti_hash": sha("jti-v1", claims["jti"]),
        "model_hash": sha("model-v1", requested_model),
        "runtime_hash": sha("runtime-v1", runtime),
        "request_hash": sha("request-v1", request_id),
        "epoch": witness["epoch"],
        "projection_generation": generation,
        "max_attempts": budget,
        "resource_witness_revocation_generation": witness["revocation_generation"],
        "provider_calls": 0,
        "sql_executed": False,
        "broker_admission_authorized": False,
        "provider_execution_authorized": False,
        "current_revocation_proven": False,
        "external_monotonic_highwater_proven": False,
    }
