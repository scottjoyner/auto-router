"""P0 #65: two-phase, verify-only allocation-set -> native slot receipt.

PHASE A is signed AFTER authenticating the forwarded original/wire-body
binding and AFTER native task IDs have been allocated, but BEFORE slots
exist. PHASE B is signed by a different independently pinned key AFTER
a native task enters a real slot. Neither producer exists in production.

Signatures in tests authenticate fabricated fixture claims, NOT physical
process truth. This module never sets a live attestation/dispatch flag,
touches a provider, stores a receipt, or trusts caller-supplied headers.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Sequence
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from auto_router.native_task_receipt_join import (
    NativeTaskJoinRejected as TwoPhaseRejected,
    _decode,
    _identifier,
    _observe_time,
    _uuid4,
)
from auto_router.p0_single_task_shape import qualify_strict_single_native_task_shape

ALLOC_DOMAIN = b"p0-native-allocation-set-v2\x00"
SLOT_DOMAIN = b"p0-native-slot-observation-v2\x00"
REFERENCE_DOMAIN = b"p0-native-allocation-set-reference-v2\x00"
_ALLOC_SCHEMA = "p0-native-allocation-set-v2"
_SLOT_SCHEMA = "p0-native-slot-observation-v2"
_AUD = "auto-router-p0-two-phase-native"
_ALLOC_ISS = "trusted-post-auth-native-allocation-fixture"
_SLOT_ISS = "independent-native-slot-observer-fixture"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TASK_KEYS = frozenset({"native_task_id", "parent_task_id", "allocation_index"})
_ALLOC_KEYS = frozenset({
    "schema", "issuer", "audience", "key_id", "gateway_issuer",
    "tenant", "subject", "root_request_id", "attempt_id",
    "request_path", "original_request_sha256", "wire_request_sha256",
    "wire_request_bytes", "model_handle", "node_id", "runtime_instance_id",
    "boot_id", "pid", "process_start_ticks", "model_artifact_sha256",
    "admission_nonce", "observed_at_ms", "allocated_tasks",
})
_SLOT_KEYS = frozenset({
    "schema", "issuer", "audience", "key_id", "allocation_receipt_sha256",
    "node_id", "runtime_instance_id", "boot_id", "pid",
    "process_start_ticks", "model_artifact_sha256",
    "native_task_id", "parent_task_id", "slot_id",
    "stage", "event_id", "seq", "observed_at_ms",
})
_PROCESS = (
    "node_id", "runtime_instance_id", "boot_id", "pid",
    "process_start_ticks", "model_artifact_sha256",
)
_MAX_WIRE_BYTES = 4096
_MAX_TASKS = 4
_PROTECTED_HEADERS = frozenset({
    "authorization", "x-api-key", "x-p0-native-admission",
    "x-p0-signature", "x-p0-original-request-sha256",
})


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX.fullmatch(value))


def _positive_int(value: Any, *, allow_zero: bool = False) -> bool:
    return type(value) is int and (0 <= value if allow_zero else 0 < value) < 2**31


def _process_ok(value: dict[str, Any]) -> bool:
    return (
        _identifier(value["node_id"])
        and _identifier(value["runtime_instance_id"])
        and _uuid4(value["boot_id"])
        and _positive_int(value["pid"])
        and _positive_int(value["process_start_ticks"])
        and _sha(value["model_artifact_sha256"])
    )


def _actor_bound(allocation: dict[str, Any], actor: dict[str, Any]) -> bool:
    return all((
        allocation["gateway_issuer"] == actor["issuer"],
        allocation["tenant"] == actor["tenant"],
        allocation["subject"] == actor["subject"],
        allocation["root_request_id"] == actor["root_request_id"],
        allocation["attempt_id"] == actor["attempt_id"],
        allocation["request_path"] == actor["request_path"],
        allocation["original_request_sha256"] == actor["request_sha256"],
        allocation["model_handle"] == actor["model_handle"],
    ))


def verify_raw_native_auth_header_shape(
    raw_pairs: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Inspect UNFLATTENED header pairs; never authenticate by this alone.

    For a future native C++ pre-routing verifier, inspect the original
    multimap BEFORE its values are copied to std::map (last-value-wins).
    A successful shape result is NOT a verified signature or human.
    """
    if not isinstance(raw_pairs, (tuple, list)) or len(raw_pairs) > 64:
        raise TwoPhaseRejected("raw HTTP header count outside bound")
    total = 0
    seen: set[str] = set()
    present: set[str] = set()
    for pair in raw_pairs:
        if not (
            isinstance(pair, tuple) and len(pair) == 2
            and all(isinstance(item, str) for item in pair)
        ):
            raise TwoPhaseRejected("raw HTTP header pair invalid")
        name, value = pair
        total += len(name) + len(value)
        if total > 8192 or len(name) > 128 or len(value) > 4096:
            raise TwoPhaseRejected("raw HTTP headers exceed budget")
        if (
            not name or name.strip() != name
            or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_\x60|~-]+", name)
            or "\r" in value or "\n" in value or "\x00" in value
        ):
            raise TwoPhaseRejected("unsafe raw HTTP header framing")
        folded = name.lower()
        if folded in _PROTECTED_HEADERS or folded.startswith("x-p0-"):
            if folded in seen:
                raise TwoPhaseRejected("duplicate protected HTTP header")
            seen.add(folded)
            present.add(folded)
    if {"authorization", "x-api-key"} <= present:
        raise TwoPhaseRejected("ambiguous native service authentication")
    return {
        "schema": "p0-raw-header-shape-v1",
        "protected_header_duplicates_rejected": True,
        "authenticated_forwarder": False,
        "authenticated_human": False,
        "signed_native_admission_verified": False,
    }


def verify_two_phase_single_task_fixture(
    *,
    verified_actor: dict[str, Any],
    original_request_body: dict[str, Any],
    forwarded_wire_body: bytes,
    signed_allocation: tuple[bytes, bytes] | None,
    signed_slots: Sequence[tuple[bytes, bytes]],
    allocation_public_key: Ed25519PublicKey,
    allocation_key_id: str,
    slot_public_key: Ed25519PublicKey,
    slot_key_id: str,
    expected_node_id: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Never derive operational authority from synthetic signatures.

    Only a trusted router caller can supply an authentic v2 ASGI actor
    and *exactly the bytes* it forwarded to the native API. This helper
    does not supply or verify that transport provenance itself.
    """
    verdict = {
        "schema": "p0-two-phase-native-fixture-verdict-v1",
        "signed_allocation_set": "unobserved",
        "signed_slot_claims": "unobserved",
        "expected_native_task_count": 1,
        "claimed_allocation_task_count": None,
        "claimed_slot_count": 0,
        "all_signed_allocations_have_slot_claims": False,
        "actual_native_task_count_observed": None,
        "native_allocation_complete": False,
        "operational_ingress_signer_proven": False,
        "operational_slot_observer_proven": False,
        "durable_replay_fencing_proven": False,
        "model_artifact_sha256_measured": False,
        "provider_execution_attested": False,
        "physical_inference_permitted": False,
        "operator_approved": False,
        "response_delivered": "unknown",
    }
    # Authenticate the original actor and narrow signed canary shape FIRST,
    # prior to parsing any synthetic native claim.
    shape = qualify_strict_single_native_task_shape(
        verified_actor=verified_actor,
        original_request_body=original_request_body,
    )
    if shape["physical_inference_permitted"] is not False:
        raise TwoPhaseRejected("unapproved execution promotion")
    if (
        not isinstance(forwarded_wire_body, bytes)
        or not 1 <= len(forwarded_wire_body) <= _MAX_WIRE_BYTES
    ):
        raise TwoPhaseRejected("bounded exact forwarded wire body required")
    if not (
        isinstance(allocation_public_key, Ed25519PublicKey)
        and isinstance(slot_public_key, Ed25519PublicKey)
        and _identifier(allocation_key_id)
        and _identifier(slot_key_id)
        and _identifier(expected_node_id)
    ):
        raise TwoPhaseRejected("independently pinned native signing keys required")
    ingress_bytes = allocation_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    slot_bytes = slot_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if hmac.compare_digest(ingress_bytes, slot_bytes):
        raise TwoPhaseRejected("native allocation and slot keys must differ")
    if not isinstance(signed_slots, (list, tuple)) or len(signed_slots) > _MAX_TASKS:
        raise TwoPhaseRejected("bounded native slot events required")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if type(now) is not int or now < 0:
        raise TwoPhaseRejected("invalid evaluation time")
    if signed_allocation is None:
        if signed_slots:
            raise TwoPhaseRejected("orphan signed native slot observations")
        return verdict
    allocation, payload = _decode(
        signed_allocation, public_key=allocation_public_key,
        domain=ALLOC_DOMAIN, expected_fields=_ALLOC_KEYS,
    )
    if (
        allocation["schema"] != _ALLOC_SCHEMA
        or allocation["issuer"] != _ALLOC_ISS
        or allocation["audience"] != _AUD
        or allocation["key_id"] != allocation_key_id
        or allocation["node_id"] != expected_node_id
        or not _process_ok(allocation)
        or not _uuid4(allocation["admission_nonce"])
        or not _observe_time(allocation["observed_at_ms"], now)
        or not _actor_bound(allocation, verified_actor)
        or not _sha(allocation["wire_request_sha256"])
        or type(allocation["wire_request_bytes"]) is not int
        or allocation["wire_request_bytes"] != len(forwarded_wire_body)
        or not hmac.compare_digest(
            allocation["wire_request_sha256"],
            hashlib.sha256(forwarded_wire_body).hexdigest(),
        )
    ):
        raise TwoPhaseRejected("native task allocation identity/body binding invalid")
    tasks = allocation["allocated_tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= _MAX_TASKS:
        raise TwoPhaseRejected("bounded native task allocation list required")
    names: set[int] = set()
    for idx, task in enumerate(tasks):
        if (
            not isinstance(task, dict)
            or task.keys() != _TASK_KEYS
            or not _positive_int(task["native_task_id"], allow_zero=True)
            or type(task["parent_task_id"]) is not int
            or not -1 <= task["parent_task_id"] < 2**31
            or type(task["allocation_index"]) is not int
            or task["allocation_index"] != idx
            or task["native_task_id"] in names
            or task["parent_task_id"] == task["native_task_id"]
        ):
            raise TwoPhaseRejected("invalid native task allocation set")
        names.add(task["native_task_id"])
    # P0 MUST NOT admit multi-task work until independent observer
    # custody and native complete allocation-set proof exist.
    if len(tasks) != 1 or tasks[0]["parent_task_id"] != -1:
        raise TwoPhaseRejected("physical canary forbids native task fanout")
    verdict["signed_allocation_set"] = "synthetic_allocation_claim_correlated"
    verdict["claimed_allocation_task_count"] = len(tasks)
    if not signed_slots:
        return verdict
    ref = hashlib.sha256(
        REFERENCE_DOMAIN + payload + signed_allocation[1]
    ).hexdigest()
    seen: set[str] = set()
    seen_tasks: set[int] = set()
    for seq, signed in enumerate(signed_slots):
        event, _raw = _decode(
            signed, public_key=slot_public_key,
            domain=SLOT_DOMAIN, expected_fields=_SLOT_KEYS,
        )
        if (
            event["schema"] != _SLOT_SCHEMA
            or event["issuer"] != _SLOT_ISS
            or event["audience"] != _AUD
            or event["key_id"] != slot_key_id
            or not _process_ok(event)
            or any(event[name] != allocation[name] for name in _PROCESS)
            or event["stage"] != "slot_assigned"
            or type(event["seq"]) is not int or event["seq"] != seq
            or not _uuid4(event["event_id"])
            or event["event_id"] in seen
            or not _positive_int(event["native_task_id"], allow_zero=True)
            or event["native_task_id"] not in names
            or event["native_task_id"] in seen_tasks
            or type(event["parent_task_id"]) is not int
            or event["parent_task_id"] != tasks[0]["parent_task_id"]
            or not _positive_int(event["slot_id"], allow_zero=True)
            or not _observe_time(event["observed_at_ms"], now)
            or event["observed_at_ms"] < allocation["observed_at_ms"]
            or not _sha(event["allocation_receipt_sha256"])
            or not hmac.compare_digest(event["allocation_receipt_sha256"], ref)
        ):
            raise TwoPhaseRejected("native slot not bound to signed allocation set")
        seen.add(event["event_id"])
        seen_tasks.add(event["native_task_id"])
    verdict["signed_slot_claims"] = "synthetic_slot_claims_correlated"
    verdict["claimed_slot_count"] = len(seen_tasks)
    verdict["all_signed_allocations_have_slot_claims"] = seen_tasks == names
    return verdict
