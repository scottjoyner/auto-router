"""P0 #37 / #61: verify-only two-signer native-task join, never execution proof.

This component correlates an independently signed *fixture* HTTP-admission
receipt with separately signed *fixture* native-process lifecycle events.
It does NOT implement a native HTTP verifier, sensor, observer signer,
dispatch path, durable anti-replay ledger, model process proof, trace sink,
or real owner-scoped receipt. In particular, a pair of colluding signers
can manufacture entirely valid evidence; return flags NEVER promote it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_INGRESS_DOMAIN = b"p0-native-admitted-task-v1\x00"
_NATIVE_DOMAIN = b"p0-native-lifecycle-observer-v1\x00"
_JOIN_DOMAIN = b"p0-native-admitted-task-reference-v1\x00"
_INGRESS_SCHEMA = "p0-native-admitted-task-v1"
_NATIVE_SCHEMA = "p0-native-task-event-v1"
_INGRESS_ISS = "post-auth-native-ingress-p0"
_NATIVE_ISS = "separate-native-process-observer-p0"
_INGRESS_AUD = "auto-router-p0-native-task-join"
_NATIVE_AUD = "auto-router-p0-native-task-join"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INGRESS_FIELDS = frozenset({
    "schema", "issuer", "audience", "key_id", "node_id",
    "runtime_instance_id", "boot_id", "pid", "process_start_ticks",
    "model_artifact_sha256", "native_task_id", "slot_id",
    "root_request_id", "attempt_id", "request_path",
    "request_sha256", "model_handle", "admission_nonce", "observed_at_ms",
})
_NATIVE_FIELDS = frozenset({
    "schema", "issuer", "audience", "key_id", "node_id",
    "runtime_instance_id", "boot_id", "pid", "process_start_ticks",
    "model_artifact_sha256", "native_task_id", "slot_id",
    "admission_receipt_sha256", "stage", "event_id", "seq",
    "observed_at_ms", "generated_tokens",
})
_NATIVE_STAGES = frozenset({
    "slot_assigned", "sampled_token", "final_result_enqueued",
    "native_error", "native_cancelled",
})
_TERMINAL_STAGES = frozenset({
    "final_result_enqueued", "native_error", "native_cancelled",
})
_MAX_EVENTS = 8


class NativeTaskJoinRejected(ValueError):
    """Fixed, sanitized error: never echo payloads, prompts or credentials."""


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise NativeTaskJoinRejected("duplicate signed evidence field")
        out[key] = value
    return out


def _encode(obj: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise NativeTaskJoinRejected("nonserializable signed evidence") from exc


def _decode(
    record: tuple[bytes, bytes],
    *,
    public_key: Ed25519PublicKey,
    domain: bytes,
    expected_fields: frozenset[str],
) -> tuple[dict[str, Any], bytes]:
    if not (
        isinstance(record, tuple) and len(record) == 2
        and isinstance(record[0], bytes)
        and 0 < len(record[0]) <= 3072
        and isinstance(record[1], bytes) and len(record[1]) == 64
    ):
        raise NativeTaskJoinRejected("bounded signed evidence required")
    payload, signature = record
    try:
        public_key.verify(signature, domain + payload)
    except Exception as exc:
        raise NativeTaskJoinRejected("native evidence signature invalid") from exc
    try:
        decoded = json.loads(payload, object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeTaskJoinRejected("native evidence JSON invalid") from exc
    if not isinstance(decoded, dict) or decoded.keys() != expected_fields:
        raise NativeTaskJoinRejected("unapproved native evidence schema")
    if _encode(decoded) != payload:
        raise NativeTaskJoinRejected("noncanonical signed evidence")
    return decoded, payload


def _uuid4(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _process_ok(value: dict[str, Any]) -> bool:
    return (
        _identifier(value["node_id"])
        and _identifier(value["runtime_instance_id"])
        and _uuid4(value["boot_id"])
        and type(value["pid"]) is int and 0 < value["pid"] < 2**31
        and type(value["process_start_ticks"]) is int
        and value["process_start_ticks"] > 0
        and isinstance(value["model_artifact_sha256"], str)
        and bool(_SHA256.fullmatch(value["model_artifact_sha256"]))
        and type(value["native_task_id"]) is int
        and 0 <= value["native_task_id"] < 2**31
        and type(value["slot_id"]) is int
        and 0 <= value["slot_id"] < 2**31
    )


def _process_tuple(value: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        value[name] for name in (
            "node_id", "runtime_instance_id", "boot_id", "pid",
            "process_start_ticks", "model_artifact_sha256",
            "native_task_id", "slot_id",
        )
    )


def _observe_time(value: Any, now_ms: int) -> bool:
    return (
        type(value) is int
        and now_ms - 120_000 <= value <= now_ms + 5_000
    )


def correlate_native_task_join(
    *,
    verified_actor: dict[str, Any],
    signed_ingress: tuple[bytes, bytes] | None,
    signed_native_events: Sequence[tuple[bytes, bytes]],
    ingress_public_key: Ed25519PublicKey,
    ingress_key_id: str,
    native_public_key: Ed25519PublicKey,
    native_key_id: str,
    expected_node_id: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Return deny-only correlation for trusted in-process verified actor.

    The actor must come from the authenticated v2 ASGI admission scope, not
    arbitrary client JSON. Both keys must be independently pinned from a
    trusted source, not read from signatures or provided by clients.
    """
    verdict = {
        "schema": "p0-native-task-join-verdict-v1",
        "admission_correlation": "unobserved",
        "native_lifecycle_correlation": "unobserved",
        "signed_native_event_count": 0,
        "native_terminal_claim_correlated": False,
        "provider_execution_attested": False,
        "physical_provider_proof": False,
        "first_token_proven": False,
        "response_delivered": "unknown",
        "encrypted_trace_custody_verified": False,
        "cross_bundle_replay_verified": False,
        "operational_signer_provenance_verified": False,
    }
    if not isinstance(verified_actor, dict):
        raise NativeTaskJoinRejected("trusted v2 actor required")
    required_actor = (
        "issuer", "tenant", "subject", "root_request_id",
        "attempt_id", "request_path", "request_sha256", "model_handle",
    )
    if (
        not all(isinstance(verified_actor.get(name), str) for name in required_actor)
        or verified_actor["issuer"] != "trusted-user-gateway-p0"
        or not _uuid4(verified_actor["root_request_id"])
        or not _uuid4(verified_actor["attempt_id"])
        or verified_actor["root_request_id"] == verified_actor["attempt_id"]
        or verified_actor["request_path"] != "/v1/chat/completions"
        or not _SHA256.fullmatch(verified_actor["request_sha256"])
        or not all(_identifier(verified_actor[k]) for k in ("tenant", "subject", "model_handle"))
    ):
        raise NativeTaskJoinRejected("v2 request-scoped actor required")
    if (
        not isinstance(ingress_public_key, Ed25519PublicKey)
        or not isinstance(native_public_key, Ed25519PublicKey)
        or not _identifier(ingress_key_id)
        or not _identifier(native_key_id)
        or not _identifier(expected_node_id)
    ):
        raise NativeTaskJoinRejected("pinned two-signer identity required")
    ingress_pk = ingress_public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    native_pk = native_public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    if hmac.compare_digest(ingress_pk, native_pk):
        raise NativeTaskJoinRejected("native observer and ingress keys must differ")
    if (
        not isinstance(signed_native_events, (tuple, list))
        or len(signed_native_events) > _MAX_EVENTS
    ):
        raise NativeTaskJoinRejected("native event sequence budget exceeded")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if type(now) is not int or now < 0:
        raise NativeTaskJoinRejected("invalid native evidence evaluation time")
    if signed_ingress is None:
        if signed_native_events:
            raise NativeTaskJoinRejected("native event without admitted task receipt")
        return verdict
    ingress, ingress_bytes = _decode(
        signed_ingress, public_key=ingress_public_key,
        domain=_INGRESS_DOMAIN, expected_fields=_INGRESS_FIELDS,
    )
    if (
        ingress["schema"] != _INGRESS_SCHEMA
        or ingress["issuer"] != _INGRESS_ISS
        or ingress["audience"] != _INGRESS_AUD
        or ingress["key_id"] != ingress_key_id
        or ingress["node_id"] != expected_node_id
        or not _process_ok(ingress)
        or not _uuid4(ingress["admission_nonce"])
        or not _observe_time(ingress["observed_at_ms"], now)
    ):
        raise NativeTaskJoinRejected("native ingress identity invalid")
    for field in (
        "root_request_id", "attempt_id", "request_path",
        "request_sha256", "model_handle",
    ):
        if ingress[field] != verified_actor[field]:
            raise NativeTaskJoinRejected("native ingress not bound to signed actor")
    verdict["admission_correlation"] = "signed_fixture_ingress_correlated"
    if not signed_native_events:
        return verdict
    reference = hashlib.sha256(_JOIN_DOMAIN + ingress_bytes).hexdigest()
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    last_token_count = 0
    for seq, record in enumerate(signed_native_events):
        event, _raw = _decode(
            record, public_key=native_public_key,
            domain=_NATIVE_DOMAIN, expected_fields=_NATIVE_FIELDS,
        )
        if (
            event["schema"] != _NATIVE_SCHEMA
            or event["issuer"] != _NATIVE_ISS
            or event["audience"] != _NATIVE_AUD
            or event["key_id"] != native_key_id
            or event["node_id"] != expected_node_id
            or not _process_ok(event)
        ):
            raise NativeTaskJoinRejected("native observer identity invalid")
        if _process_tuple(event) != _process_tuple(ingress):
            raise NativeTaskJoinRejected("native process or task ID substituted")
        if not (
            isinstance(event["admission_receipt_sha256"], str)
            and hmac.compare_digest(event["admission_receipt_sha256"], reference)
        ):
            raise NativeTaskJoinRejected("observer event not bound to signed ingress")
        if (
            not _uuid4(event["event_id"]) or event["event_id"] in seen
            or type(event["seq"]) is not int or event["seq"] != seq
            or not _observe_time(event["observed_at_ms"], now)
            or event["observed_at_ms"] < ingress["observed_at_ms"]
            or (events and event["observed_at_ms"] < events[-1]["observed_at_ms"])
            or type(event["generated_tokens"]) is not int
            or not 0 <= event["generated_tokens"] < 1_000_000
            or event["stage"] not in _NATIVE_STAGES
        ):
            raise NativeTaskJoinRejected("native event sequence, freshness or budget invalid")
        if seq == 0 and (
            event["stage"] != "slot_assigned"
            or event["generated_tokens"] != 0
        ):
            raise NativeTaskJoinRejected("native slot assignment must lead sequence")
        if seq > 0:
            if events[-1]["stage"] in _TERMINAL_STAGES:
                raise NativeTaskJoinRejected("native event after terminal")
            if event["stage"] == "slot_assigned":
                raise NativeTaskJoinRejected("duplicate native slot assignment")
            if event["stage"] == "sampled_token":
                if event["generated_tokens"] <= last_token_count:
                    raise NativeTaskJoinRejected("sampled token count not increasing")
            elif event["generated_tokens"] < last_token_count:
                raise NativeTaskJoinRejected("terminal generated count regressed")
        if event["stage"] == "sampled_token":
            last_token_count = event["generated_tokens"]
        seen.add(event["event_id"])
        events.append(event)
    terminal = bool(events and events[-1]["stage"] in _TERMINAL_STAGES)
    verdict["native_lifecycle_correlation"] = (
        "signed_fixture_terminal_claim" if terminal else "signed_fixture_partial"
    )
    verdict["native_terminal_claim_correlated"] = terminal
    verdict["signed_native_event_count"] = len(events)
    return verdict
