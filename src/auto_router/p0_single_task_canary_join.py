"""P0 #37: compose narrow signed-body shape and dual-signer native correlation.

RESEARCH ONLY. No live middleware installation, native observer, physical
provider execution, model dispatch, trace write or production release switch.
The trusted actor MUST originate from verified v2 ASGI scope; this does not
authenticate caller-supplied dictionaries or signer operational provenance.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from auto_router.native_task_receipt_join import (
    NativeTaskJoinRejected,
    correlate_native_task_join,
)
from auto_router.p0_single_task_shape import qualify_strict_single_native_task_shape


def correlate_strict_single_task_canary_fixture(
    *,
    verified_actor: dict[str, Any],
    original_request_body: dict[str, Any],
    signed_ingress: tuple[bytes, bytes] | None,
    signed_native_events: Sequence[tuple[bytes, bytes]],
    ingress_public_key: Ed25519PublicKey,
    ingress_key_id: str,
    native_public_key: Ed25519PublicKey,
    native_key_id: str,
    expected_node_id: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Qualify exact signed one-task SHAPE before accepting fixture joins.

    Native allocation completion is still UNKNOWN. A post-auth fixture
    receipt that says "task 42" cannot prove no child task 43 existed.
    """
    shape = qualify_strict_single_native_task_shape(
        verified_actor=verified_actor,
        original_request_body=original_request_body,
    )
    claim = correlate_native_task_join(
        verified_actor=verified_actor,
        signed_ingress=signed_ingress,
        signed_native_events=signed_native_events,
        ingress_public_key=ingress_public_key,
        ingress_key_id=ingress_key_id,
        native_public_key=native_public_key,
        native_key_id=native_key_id,
        expected_node_id=expected_node_id,
        now_ms=now_ms,
    )
    if claim["provider_execution_attested"] or claim["physical_provider_proof"]:
        raise NativeTaskJoinRejected("native fixture proof promotion not permitted")
    return {
        **claim,
        "canary_shape": shape["shape"],
        "expected_native_task_count": 1,
        "actual_native_task_count_observed": None,
        "native_parent_child_lineage_proven": False,
        "native_allocation_complete": False,
        "physical_inference_permitted": False,
        "operator_approved": False,
    }
