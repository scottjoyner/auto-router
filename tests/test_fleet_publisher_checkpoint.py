"""Offline signed observer checkpoint tests. No production API/keys/storage touched."""
from __future__ import annotations

import base64
import shutil
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router.fleet_publisher_checkpoint import (
    ObserverCheckpointError,
    TrustedObserver,
    checkpoint_signing_message,
    compare_latest_checkpoint,
    snapshot_local_highwater,
)
from auto_router.fleet_publisher_envelope import LocalSQLiteReplayFence

NOW = 1792585000000
A = "joyner-model-catalog"
B = "joyner-gpu-heartbeat"


@pytest.fixture
def keys():
    return {
        "observer": Ed25519PrivateKey.generate(),
        "catalog": Ed25519PrivateKey.generate(),
        "presence": Ed25519PrivateKey.generate(),
        "attacker": Ed25519PrivateKey.generate(),
    }


@pytest.fixture
def root(keys):
    return TrustedObserver(
        observer_id="lenovo-independent-observer",
        key_id="lenovo-observer-v1",
        verify_key=keys["observer"].public_key(),
        publisher_ids=(A, B),
        publisher_keys=(keys["catalog"].public_key(), keys["presence"].public_key()),
    )


@pytest.fixture
def store(tmp_path):
    path=tmp_path / "replay.sqlite3"
    fence=LocalSQLiteReplayFence(path)
    fence.consume(A, 1)
    fence.consume(B, 1)
    return path


def signed(keys, *, generation=1, highwater=None, revoked=None, signer="observer",
           observer="lenovo-independent-observer", key_id="lenovo-observer-v1",
           issued=NOW, expires=NOW+30000, schema="fleet-independent-checkpoint.v1"):
    doc={
        "schema":schema,
        "observer_id":observer,
        "key_id":key_id,
        "generation":generation,
        "issued_at_ms":issued,
        "expires_at_ms":expires,
        "highwater":({A:1,B:1} if highwater is None else highwater),
        "revoked_publishers":([] if revoked is None else revoked),
        "signature":"0"*86,
    }
    signature=keys[signer].sign(checkpoint_signing_message(doc))
    doc["signature"]=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return doc


def check(keys, root, store, doc=None, generation=1, now=NOW):
    return compare_latest_checkpoint(
        signed(keys) if doc is None else doc,
        trusted_observer=root, externally_pinned_generation=generation,
        local_database=store, now_ms=now,
    )


def rejection(keys, root, store, doc, generation=1, now=NOW):
    with pytest.raises(ObserverCheckpointError):
        check(keys,root,store,doc,generation,now)


def test_matching_independent_checkpoint_remains_no_go(keys,root,store):
    result=check(keys,root,store)
    assert result.observer_signature_valid
    assert result.local_state_matches_checkpoint
    assert result.generation_matches_external_anchor
    assert not result.externally_anchored_latest_verified
    assert not result.publisher_custody_verified
    assert not result.external_authority_custody_verified
    assert not result.live_replay_protection_verified
    assert not result.gpu_residency_verified
    assert result.production_admission=="NO_GO"


def test_missing_independent_checkpoint_fails_closed(keys,root,store):
    with pytest.raises(ObserverCheckpointError, match="generation"):
        check(keys,root,store,doc=signed(keys), generation=None)
    with pytest.raises(ObserverCheckpointError, match="unavailable"):
        compare_latest_checkpoint(
            None,trusted_observer=root,externally_pinned_generation=1,
            local_database=store,now_ms=NOW,
        )


def test_independent_checkpoint_detects_local_db_rollback(keys,root,store,tmp_path):
    old=tmp_path/"older.sqlite3"
    shutil.copyfile(store,old)
    fence=LocalSQLiteReplayFence(store)
    fence.consume(A,2)
    latest=signed(keys,generation=2,highwater={A:2,B:1})
    assert check(keys,root,store,latest,generation=2).production_admission=="NO_GO"
    # Rollback both file contents and verifier instance (same path now old bytes).
    shutil.copyfile(old,store)
    with pytest.raises(ObserverCheckpointError, match="differs"):
        check(keys,root,store,latest,generation=2)


def test_separately_pinned_generation_rejects_old_signed_checkpoint(keys,root,store):
    old=signed(keys,generation=1)
    with pytest.raises(ObserverCheckpointError,match="generation"):
        check(keys,root,store,old,generation=2)


def test_if_both_anchor_and_local_db_roll_back_it_cannot_detect_it(keys,root,store,tmp_path):
    """EXPECTED SECURITY LIMIT: no externally controlled current-generation service."""
    older=tmp_path/"older.sqlite3"
    shutil.copyfile(store,older)
    old_cp=signed(keys,generation=1)
    LocalSQLiteReplayFence(store).consume(A,2)
    newer=signed(keys,generation=2,highwater={A:2,B:1})
    assert check(keys,root,store,newer,generation=2).production_admission=="NO_GO"
    shutil.copyfile(older,store)
    compromised=check(keys,root,store,old_cp,generation=1)
    assert compromised.production_admission=="NO_GO"
    assert not compromised.externally_anchored_latest_verified


def test_old_checkpoint_rejected_even_if_signed_by_expected_key(keys,root,store):
    rejection(keys,root,store,signed(keys,expires=NOW-1))


def test_checkpoint_from_wrong_observer_rejected(keys,root,store):
    rejection(keys,root,store,signed(keys,observer="xwing"))


def test_wrong_observer_key_id_rejected(keys,root,store):
    rejection(keys,root,store,signed(keys,key_id="unknown-key"))


def test_wrong_private_key_rejected(keys,root,store):
    rejection(keys,root,store,signed(keys,signer="attacker"))


def test_publisher_private_key_not_authorized_for_observer(keys,root,store):
    rejection(keys,root,store,signed(keys,signer="catalog"))


def test_observer_key_cannot_be_reused_from_publisher(keys):
    with pytest.raises(ValueError,match="cannot reuse"):
        TrustedObserver("observer","key",keys["catalog"].public_key(),(A,B),
                        publisher_keys=(keys["catalog"].public_key(),))


def test_modified_signed_highwater_rejected(keys,root,store):
    doc=signed(keys)
    doc["highwater"][A]=2
    rejection(keys,root,store,doc)


def test_unauthorized_extra_publisher_rejected_even_if_signed(keys,root,store):
    rejection(keys,root,store,signed(keys,highwater={A:1,B:1,"xwing":9}))


def test_missing_publisher_fails_closed(keys,root,store):
    rejection(keys,root,store,signed(keys,highwater={A:1}))


def test_signed_highwater_mismatch_rejected(keys,root,store):
    rejection(keys,root,store,signed(keys,highwater={A:2,B:1}))


def test_revoked_publisher_fails_closed(keys,root,store):
    rejection(keys,root,store,signed(keys,revoked=[A]))


def test_extra_unknown_revocation_fails_closed(keys,root,store):
    rejection(keys,root,store,signed(keys,revoked=["rogue"]))


def test_revoked_flag_tampering_invalidates_signature(keys,root,store):
    doc=signed(keys)
    doc["revoked_publishers"]=[A]
    rejection(keys,root,store,doc)


def test_no_local_db_fails_closed(keys,root,tmp_path):
    rejection(keys,root,tmp_path/"missing.sqlite3",signed(keys))


def test_corrupted_db_fails_closed(keys,root,store):
    store.write_bytes(b"not SQLite")
    rejection(keys,root,store,signed(keys))


def test_local_db_missing_entry_fails_closed(keys,root,store):
    with sqlite3.connect(store) as conn:
        conn.execute("DELETE FROM publisher_replay_highwater WHERE publisher_id=?",(B,))
    rejection(keys,root,store,signed(keys))


def test_local_db_extra_entry_fails_closed(keys,root,store):
    LocalSQLiteReplayFence(store).consume("unexpected-publisher",1)
    rejection(keys,root,store,signed(keys))


def test_future_issued_at_rejected(keys,root,store):
    rejection(keys,root,store,signed(keys,issued=NOW+16000,expires=NOW+35000))


def test_oversized_lease_rejected(keys,root,store):
    rejection(keys,root,store,signed(keys,expires=NOW+121000))


def test_invalid_checkpoint_generation_type_rejected(keys,root,store):
    rejection(keys,root,store,signed(keys,generation=True))


@pytest.mark.parametrize("generation",[None, True, 0, -1, "1", 2])
def test_invalid_or_unpinned_generation_fail_closed(keys,root,store,generation):
    rejection(keys,root,store,signed(keys),generation=generation)


def test_extra_checkpoint_fields_rejected(keys,root,store):
    doc=signed(keys)
    doc["unknown"]="SECRET"
    rejection(keys,root,store,doc)


def test_observer_checkpoint_cannot_claim_runtime_models(keys,root,store):
    doc=signed(keys)
    doc["loaded_model"]="joyner-toolcall"
    rejection(keys,root,store,doc)


def test_rejected_error_never_leaks_raw_document_data(keys,root,store):
    doc=signed(keys)
    doc["secret"]="MyHiddenAPIKEY"
    with pytest.raises(ObserverCheckpointError) as exc:
        check(keys,root,store,doc)
    assert "MyHiddenAPIKEY" not in str(exc.value)


def test_readonly_snapshot_produces_exact_local_sequences(store):
    assert snapshot_local_highwater(store)=={A:1,B:1}


def test_signature_domain_separated(keys):
    assert checkpoint_signing_message(signed(keys)).startswith(
        b"auto-router:fleet-independent-checkpoint:v1\x00"
    )