"""P0 #37 independent signed node-observer correlation, NEVER physical proof.

This module verifies *syntax, source key and identity correlation* of bounded
detached node-signed observations for a verified router request. It has NO
process instrumentation, trace-sink write, device readback or live execution
authority. A signed claim from a fixture node is not evidence the provider
actually ran; provider_execution_attested is therefore ALWAYS False.

The future physical observer MUST independently bind OS boot/process start,
actual resident model artifact and *native per-request* received/terminal
events before any other layer may consider a real execution promotion.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DOMAIN = b"p0-provider-observer-v1\x00"
_SCHEMA = "p0-provider-attempt-observation-v1"
_ISSUER = "independent-node-observer-p0"
_AUDIENCE = "auto-router-p0-provider-correlation"
_STAGE = ("resident_snapshot", "attempt_received", "native_first_token", "attempt_terminal")
_TERMINAL = {"completed", "failed", "cancelled", "incomplete"}
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:@/-]{0,127}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_EVENT_FIELDS = {
    "schema", "issuer", "audience", "key_id", "root_request_id",
    "attempt_id", "request_path", "request_sha256", "model_handle", "node_id",
    "runtime_instance_id", "boot_id", "pid", "process_start_ticks",
    "model_artifact_sha256", "provider_request_id", "stage", "terminal",
    "observed_at_ms", "event_id", "seq",
}


class ProviderWitnessRejected(ValueError):
    """A correlation attempt violates a cryptographic or provenance invariant."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in pairs:
        if key in fields:
            raise ProviderWitnessRejected("duplicate signed observer field")
        fields[key] = value
    return fields


def _canonical(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ProviderWitnessRejected("invalid observer evidence") from exc


def _uuid4(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _name(value: Any) -> bool:
    return isinstance(value, str) and bool(_NAME.fullmatch(value))


def _decode(
    record: tuple[bytes, bytes],
    observer_public_key: Ed25519PublicKey,
) -> dict[str, Any]:
    if not (
        isinstance(record, tuple) and len(record) == 2
        and isinstance(record[0], bytes)
        and 0 < len(record[0]) <= 3072
        and isinstance(record[1], bytes) and len(record[1]) == 64
    ):
        raise ProviderWitnessRejected("bounded signed observation required")
    payload, signature = record
    try:
        observer_public_key.verify(signature, DOMAIN + payload)
    except Exception as exc:
        raise ProviderWitnessRejected("untrusted node observer signature") from exc
    try:
        decoded = json.loads(payload, object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderWitnessRejected("invalid node observer envelope") from exc
    if not isinstance(decoded, dict) or set(decoded) != _EVENT_FIELDS:
        raise ProviderWitnessRejected("unapproved observer event schema")
    if _canonical(decoded) != payload:
        raise ProviderWitnessRejected("noncanonical observer event bytes")
    return decoded


def correlate_provider_observations(
    *,
    verified_actor: dict[str, Any],
    signed_observations: Sequence[tuple[bytes, bytes]],
    observer_public_key: Ed25519PublicKey,
    observer_key_id: str,
    expected_node_id: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Correlate signed events with one ALREADY VERIFIED internal router actor.

    No parsed client/user metadata is accepted as proof of a verified_actor:
    production callers may only obtain it from the signed-delegation ASGI
    scope. This API deliberately lacks an evidence promotion switch.
    """
    if not isinstance(verified_actor, dict):
        raise ProviderWitnessRejected("verified actor required")
    required_actor = (
        "issuer", "tenant", "subject", "root_request_id", "attempt_id",
        "model_handle", "request_path", "request_sha256",
    )
    if (
        not all(isinstance(verified_actor.get(key), str) for key in required_actor)
        or verified_actor.get("issuer") != "trusted-user-gateway-p0"
        or not _uuid4(verified_actor["root_request_id"])
        or not _uuid4(verified_actor["attempt_id"])
        or not _DIGEST.fullmatch(verified_actor["request_sha256"])
        or verified_actor["request_path"] != "/v1/chat/completions"
    ):
        raise ProviderWitnessRejected("v2 verified request scope required")
    if not (
        isinstance(observer_public_key, Ed25519PublicKey)
        and _name(observer_key_id)
        and _name(expected_node_id)
    ):
        raise ProviderWitnessRejected("pinned independent observer identity required")
    if not isinstance(signed_observations, (tuple, list)):
        raise ProviderWitnessRejected("observation sequence required")
    if len(signed_observations) > 4:
        raise ProviderWitnessRejected("observer event budget exceeded")
    current = int(time.time() * 1000) if now_ms is None else now_ms
    if type(current) is not int or current < 0:
        raise ProviderWitnessRejected("invalid observation evaluation time")

    # No observations is an expected fail-closed state, NOT an error and
    # certainly not inferred execution from a configured model or SSE [DONE].
    verdict = {
        "schema": "p0-provider-correlation-v1",
        "observer_correlation": "unobserved",
        "physical_provider_proof": False,
        "provider_execution_attested": False,
        "response_delivered": "unknown",
        "first_token_proven": False,
        "native_attempt_terminal_observed": False,
        "unverified_source_limitation": "signed_observer_not_independent_process_proof",
    }
    if not signed_observations:
        return verdict

    events = []
    seen_ids: set[str] = set()
    binding: tuple[Any, ...] | None = None
    for offset, record in enumerate(signed_observations):
        event = _decode(record, observer_public_key)
        if (
            event["schema"] != _SCHEMA or event["issuer"] != _ISSUER
            or event["audience"] != _AUDIENCE
            or event["key_id"] != observer_key_id
        ):
            raise ProviderWitnessRejected("observer issuer/audience/key mismatch")
        for field in (
            "root_request_id", "attempt_id", "request_path",
            "request_sha256", "model_handle",
        ):
            if event[field] != verified_actor[field]:
                raise ProviderWitnessRejected("node event does not match verified attempt")
        if event["node_id"] != expected_node_id:
            raise ProviderWitnessRejected("node identity mismatch")
        if not all(
            _name(event[field]) for field in (
                "runtime_instance_id", "provider_request_id",
            )
        ):
            raise ProviderWitnessRejected("process/request identity not pinned")
        if not (
            _uuid4(event["boot_id"]) and _uuid4(event["event_id"])
            and isinstance(event["model_artifact_sha256"], str)
            and _DIGEST.fullmatch(event["model_artifact_sha256"])
            and type(event["pid"]) is int and 0 < event["pid"] < 2**31
            and type(event["process_start_ticks"]) is int
            and event["process_start_ticks"] > 0
            and type(event["seq"]) is int and event["seq"] == offset
            and type(event["observed_at_ms"]) is int
            and current - 120000 <= event["observed_at_ms"] <= current + 5000
        ):
            raise ProviderWitnessRejected("invalid process identity, order or freshness")
        if event["event_id"] in seen_ids:
            raise ProviderWitnessRejected("duplicate observer event identity")
        seen_ids.add(event["event_id"])
        process = (
            event["node_id"], event["runtime_instance_id"],
            event["boot_id"], event["pid"], event["process_start_ticks"],
            event["model_artifact_sha256"], event["provider_request_id"],
        )
        if binding is None:
            binding = process
        elif process != binding:
            raise ProviderWitnessRejected("observer process or provider request changed")
        if event["stage"] not in _STAGE:
            raise ProviderWitnessRejected("invalid observer event stage")
        if event["stage"] != "attempt_terminal" and event["terminal"] is not None:
            raise ProviderWitnessRejected("terminal state on nonterminal stage")
        if event["stage"] == "attempt_terminal" and event["terminal"] not in _TERMINAL:
            raise ProviderWitnessRejected("invalid native terminal status")
        if events and event["observed_at_ms"] < events[-1]["observed_at_ms"]:
            raise ProviderWitnessRejected("out-of-order observer timestamp")
        events.append(event)

    stages = [event["stage"] for event in events]
    expected = ["resident_snapshot", "attempt_received"]
    if len(stages) >= 3 and stages[2] == "native_first_token":
        expected.append("native_first_token")
    if len(stages) > len(expected):
        expected.append("attempt_terminal")
    if stages != expected and stages != expected[:len(stages)]:
        raise ProviderWitnessRejected("inconsistent node observation lifecycle")
    # A completed request with no native first-token event may be a legal
    # zero-token response. Never derive TTFT from first network chunk.
    complete = (
        len(stages) >= 3 and stages[-1] == "attempt_terminal"
        and stages[:2] == ["resident_snapshot", "attempt_received"]
    )
    verdict["observer_correlation"] = (
        "signed_terminal_correlated" if complete else "signed_partial_only"
    )
    verdict["native_attempt_terminal_observed"] = bool(complete)
    verdict["first_token_proven"] = False
    return verdict

