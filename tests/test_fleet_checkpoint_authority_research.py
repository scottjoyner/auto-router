"""Synthetic observer CAS/issuance suite. No network, production keys or services."""
from __future__ import annotations

import base64
import concurrent.futures
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router.fleet_checkpoint_authority_research import (
    OfflineCheckpointAuthority,
    ResearchAuthorityError,
)
from auto_router.fleet_publisher_checkpoint import (
    ObserverCheckpointError,
    TrustedObserver,
    compare_latest_checkpoint,
)
from auto_router.fleet_publisher_envelope import (
    LocalSQLiteReplayFence,
    TrustedPublisher,
    signing_message,
)

NOW = 1792585000000
CAT = "joyner-model-catalog"
GPU = "joyner-gpu-heartbeat"
OBSERVER = "lenovo-independent-observer"
KEY_ID = "lenovo-observer-v1"


@pytest.fixture
def keys():
    return {
        CAT: Ed25519PrivateKey.generate(),
        GPU: Ed25519PrivateKey.generate(),
        "observer": Ed25519PrivateKey.generate(),
        "attacker": Ed25519PrivateKey.generate(),
    }


@pytest.fixture
def roots(keys):
    return {
        CAT: TrustedPublisher(
            CAT, "joyner", "model_catalog", "catalog-key",
            keys[CAT].public_key(), ("joyner-toolcall", "joyner-ling"),
        ),
        GPU: TrustedPublisher(
            GPU, "joyner", "gpu_presence", "gpu-key", keys[GPU].public_key(),
        ),
    }


@pytest.fixture
def context(tmp_path, keys, roots):
    path = tmp_path / "observer.sqlite3"
    authority = OfflineCheckpointAuthority(
        path, observer_id=OBSERVER, key_id=KEY_ID,
        private_key=keys["observer"], trust_roots=roots,
        bootstrap_highwater={CAT: 1, GPU: 1},
        initial_issued_at_ms=NOW,
    )
    local_path = tmp_path / "reporter.sqlite3"
    f = LocalSQLiteReplayFence(local_path)
    f.consume(CAT, 1)
    f.consume(GPU, 1)
    observer = TrustedObserver(
        OBSERVER, KEY_ID, keys["observer"].public_key(), (CAT, GPU),
        publisher_keys=(keys[CAT].public_key(), keys[GPU].public_key()),
    )
    return authority, local_path, observer


def signed(keys, who=CAT, seq=2, *, signer=None, issued=NOW):
    is_presence = who == GPU
    payload = (
        {"gpu_canary_guarded": True, "dispatch_admitted": False}
        if is_presence else {"library": ["joyner-toolcall"], "loaded": []}
    )
    doc = {
        "schema": "fleet-publisher-envelope.v1",
        "publisher_id": who,
        "node_id": "joyner",
        "role": "gpu_presence" if is_presence else "model_catalog",
        "key_id": "gpu-key" if is_presence else "catalog-key",
        "sequence": seq,
        "issued_at_ms": issued,
        "expires_at_ms": issued + 30000,
        "payload": payload,
        "signature": "0" * 86,
    }
    sig = keys[signer or who].sign(signing_message(doc))
    doc["signature"] = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return doc


def publish(auth, keys, who=CAT, seq=2, expected=1, issued=NOW+1000):
    return auth.publish_signed_report(
        signed(keys, who=who, seq=seq), expected_generation=expected,
        issued_at_ms=issued,
    )


def compare(checkpoint, observer, local_path, *, pinned, now=NOW+2000):
    return compare_latest_checkpoint(
        checkpoint, trusted_observer=observer,
        externally_pinned_generation=pinned,
        local_database=local_path, now_ms=now,
    )


def test_genesis_signed_checkpoint_is_observer_separated(context):
    authority, local_path, observer = context
    doc = authority.latest()
    assert doc["generation"] == 1
    assert doc["highwater"] == {CAT: 1, GPU: 1}
    assert compare(doc, observer, local_path, pinned=1).production_admission == "NO_GO"


def test_atomic_publish_signature_and_local_comparison(context, keys):
    auth, local_path, observer = context
    updated = publish(auth, keys)
    assert updated["generation"] == 2
    assert updated["highwater"] == {CAT: 2, GPU: 1}
    assert updated["signature"] != auth.private_key
    # Observer update does not mutate the publisher's independent local DB.
    with pytest.raises(ObserverCheckpointError, match="differs"):
        compare(updated, observer, local_path, pinned=2)
    LocalSQLiteReplayFence(local_path).consume(CAT, 2)
    report = compare(auth.latest(), observer, local_path, pinned=2)
    assert report.production_admission == "NO_GO"
    assert not report.externally_anchored_latest_verified


def test_same_generation_atomic_cas_rejects_second_update(context, keys):
    auth, _, _ = context
    publish(auth, keys, seq=2, expected=1)
    with pytest.raises(ResearchAuthorityError, match="CAS"):
        publish(auth, keys, who=GPU, seq=2, expected=1)
    published = publish(auth, keys, who=GPU, seq=2, expected=2)
    assert published["generation"] == 3
    assert published["highwater"] == {CAT: 2, GPU: 2}


def test_replay_after_restart_rejected(context, keys, roots):
    auth, _, _ = context
    publish(auth, keys, seq=2)
    restarted = OfflineCheckpointAuthority(
        auth.path, observer_id=OBSERVER, key_id=KEY_ID,
        private_key=keys["observer"], trust_roots=roots,
        bootstrap_highwater={CAT: 1, GPU: 1}, initial_issued_at_ms=NOW,
    )
    assert restarted.latest()["generation"] == 2
    with pytest.raises(ResearchAuthorityError, match="replay"):
        publish(restarted, keys, seq=2, expected=2)
    assert publish(restarted, keys, seq=3, expected=2)["generation"] == 3


def test_concurrent_writers_same_expected_generation_one_wins(context, keys, roots):
    auth, _, _ = context
    barrier=threading.Barrier(12)
    def attempt(i):
        independent=OfflineCheckpointAuthority(
            auth.path, observer_id=OBSERVER, key_id=KEY_ID,
            private_key=keys["observer"], trust_roots=roots,
            bootstrap_highwater={CAT:1,GPU:1}, initial_issued_at_ms=NOW,
        )
        barrier.wait(timeout=6)
        try:
            publish(independent, keys, seq=2, expected=1)
            return True
        except ResearchAuthorityError:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        outcomes=list(pool.map(attempt, range(12)))
    assert outcomes.count(True)==1
    assert outcomes.count(False)==11
    assert auth.latest()["generation"]==2


def test_unsigned_or_forged_publisher_does_not_advance_state(context, keys):
    auth, _, _ = context
    evil=signed(keys, signer="attacker")
    with pytest.raises(ResearchAuthorityError, match="signed publisher"):
        auth.publish_signed_report(evil,expected_generation=1,issued_at_ms=NOW+1000)
    assert auth.latest()["generation"]==1


def test_wrong_role_signed_source_rejected(context, keys):
    auth, _, _=context
    doc=signed(keys)
    doc["role"]="gpu_presence"
    with pytest.raises(ResearchAuthorityError):
        auth.publish_signed_report(doc,expected_generation=1,issued_at_ms=NOW+1000)
    assert auth.latest()["generation"]==1


def test_expired_signed_report_is_rejected(context, keys):
    auth, _, _ = context
    with pytest.raises(ResearchAuthorityError, match="signed publisher"):
        auth.publish_signed_report(signed(keys),expected_generation=1,
                                  issued_at_ms=NOW+30000)
    assert auth.latest()["generation"]==1


def test_revocation_is_atomic_signed_and_denies_future_reports(context, keys):
    auth, _, observer=context
    revoked=auth.revoke(CAT,expected_generation=1,issued_at_ms=NOW+1000)
    assert revoked["revoked_publishers"]==[CAT]
    assert revoked["generation"]==2
    with pytest.raises(ResearchAuthorityError,match="revoked"):
        publish(auth,keys,expected=2)
    with pytest.raises(ObserverCheckpointError,match="revoked"):
        compare(revoked,observer,context[1],pinned=2)
    assert auth.latest()["generation"]==2


def test_duplicate_revoke_and_wrong_expected_generation_fail(context):
    auth, _, _=context
    auth.revoke(CAT,expected_generation=1,issued_at_ms=NOW+1000)
    with pytest.raises(ResearchAuthorityError,match="CAS"):
        auth.revoke(GPU,expected_generation=1,issued_at_ms=NOW+1500)
    with pytest.raises(ResearchAuthorityError,match="already revoked"):
        auth.revoke(CAT,expected_generation=2,issued_at_ms=NOW+2000)


def test_unknown_publisher_revocation_fails_closed(context):
    auth, _, _=context
    with pytest.raises(ResearchAuthorityError,match="unknown"):
        auth.revoke("somebody",expected_generation=1,issued_at_ms=NOW+1000)


def test_restarting_with_different_observer_key_fails_closed(context, keys, roots):
    auth, _, _ = context
    with pytest.raises(ResearchAuthorityError,match="configuration"):
        OfflineCheckpointAuthority(
            auth.path,observer_id=OBSERVER,key_id=KEY_ID,
            private_key=keys["attacker"],trust_roots=roots,
            bootstrap_highwater={CAT:1,GPU:1},initial_issued_at_ms=NOW,
        )


def test_restarting_with_different_publisher_key_fails_closed(context, keys, roots):
    auth, _, _=context
    modified=dict(roots)
    modified[CAT]=TrustedPublisher(CAT,"joyner","model_catalog","catalog-key",
                                    keys["attacker"].public_key(),("joyner-toolcall",))
    with pytest.raises(ResearchAuthorityError,match="configuration"):
        OfflineCheckpointAuthority(
            auth.path,observer_id=OBSERVER,key_id=KEY_ID,
            private_key=keys["observer"],trust_roots=modified,
            bootstrap_highwater={CAT:1,GPU:1},initial_issued_at_ms=NOW,
        )


def test_corrupt_stored_checkpoint_must_not_be_treated_as_proof(context):
    auth, local, observer = context
    with sqlite3.connect(auth.path) as c:
        c.execute(
            "UPDATE observer_state SET checkpoint=? WHERE id=1",
            ('{"generation":1,"payload":"forged"}',),
        )
    # latest() is a raw signed-document read, not a trust assertion.
    latest = auth.latest()
    assert latest["generation"] == 1
    with pytest.raises(ObserverCheckpointError):
        compare(latest, observer, local, pinned=1)


def test_modified_observer_document_signature_must_be_checked(context):
    auth, local, observer = context
    document = auth.latest()
    document["highwater"][CAT] = 9
    with pytest.raises(ObserverCheckpointError, match="mismatch"):
        compare(document, observer, local, pinned=1)


def test_expired_observer_checkpoint_fails_closed(context):
    auth, local, observer = context
    doc = auth.latest()
    with pytest.raises(ObserverCheckpointError, match="expired"):
        compare(doc, observer, local, pinned=1, now=NOW+30000)


def test_local_db_tampering_breaks_observer_match(context):
    auth, local, observer = context
    LocalSQLiteReplayFence(local).consume(CAT, 2)
    with pytest.raises(ObserverCheckpointError, match="differs"):
        compare(auth.latest(), observer, local, pinned=1)


def test_changed_sqlite_file_path_detected_while_process_alive(context, tmp_path):
    auth, _, _ = context
    replacement=tmp_path/"observer-replacement.sqlite3"
    shutil.copyfile(auth.path,replacement)
    auth.path.unlink()
    replacement.rename(auth.path)
    with pytest.raises(ResearchAuthorityError, match="replaced"):
        auth.latest()


def test_local_and_observer_rollback_against_fresh_pinned_anchor_detected(context,keys,tmp_path):
    auth, local, observer=context
    older_local=tmp_path/"old-local.snapshot"
    older_observer=tmp_path/"old-observer.snapshot"
    shutil.copyfile(local,older_local)
    shutil.copyfile(auth.path,older_observer)
    latest=publish(auth,keys)
    LocalSQLiteReplayFence(local).consume(CAT,2)
    assert compare(latest,observer,local,pinned=2).production_admission=="NO_GO"
    shutil.copyfile(older_local,local)
    shutil.copyfile(older_observer,auth.path)
    # External generation 2 was retained outside these mutable SQLite files.
    with pytest.raises(ObserverCheckpointError,match="generation"):
        compare(auth.latest(),observer,local,pinned=2)


def test_unprotected_generation_anchor_rolls_back_with_both_databases(context,keys,tmp_path,roots):
    """Known NO_GO: requester using the rolled-back authority as its own anchor."""
    auth, local, observer=context
    older_local=tmp_path/"old-local.snapshot"
    older_observer=tmp_path/"old-observer.snapshot"
    shutil.copyfile(local,older_local)
    shutil.copyfile(auth.path,older_observer)
    publish(auth,keys)
    LocalSQLiteReplayFence(local).consume(CAT,2)
    assert compare(auth.latest(),observer,local,pinned=2).production_admission=="NO_GO"
    shutil.copyfile(older_local,local)
    shutil.copyfile(older_observer,auth.path)
    rolled=OfflineCheckpointAuthority(
        auth.path,observer_id=OBSERVER,key_id=KEY_ID,
        private_key=keys["observer"],trust_roots=roots,
        bootstrap_highwater={CAT:1,GPU:1},initial_issued_at_ms=NOW,
    )
    old=rolled.latest()
    result=compare(old,observer,local,pinned=old["generation"])
    assert result.production_admission=="NO_GO"
    assert not result.externally_anchored_latest_verified


def test_only_disposable_absolute_paths_allowed(tmp_path,keys,roots,monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match="absolute"):
        OfflineCheckpointAuthority(
            Path("relative.sqlite3"),observer_id=OBSERVER,key_id=KEY_ID,
            private_key=keys["observer"],trust_roots=roots,
            bootstrap_highwater={CAT:1,GPU:1},initial_issued_at_ms=NOW,
        )


def test_observer_reuses_publisher_key_fails_closed(tmp_path,keys,roots):
    with pytest.raises(ValueError,match="reused"):
        OfflineCheckpointAuthority(
            tmp_path/"db.sqlite3",observer_id=OBSERVER,key_id=KEY_ID,
            private_key=keys[CAT],trust_roots=roots,
            bootstrap_highwater={CAT:1,GPU:1},initial_issued_at_ms=NOW,
        )


def test_absent_db_file_not_created_during_readback(context):
    auth,_,_=context
    auth.path.unlink()
    with pytest.raises(ResearchAuthorityError,match="unavailable"):
        auth.latest()
    assert not auth.path.exists()