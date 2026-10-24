"""SQLite replay research: restart/concurrency safety, explicit rollback NO_GO."""
from __future__ import annotations

import base64
import concurrent.futures
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router.fleet_publisher_envelope import (
    LocalSQLiteReplayFence,
    PublisherEnvelopeError,
    SignedFleetPublisherVerifier,
    TrustedPublisher,
    signing_message,
)

PUBLISHER = "joyner-model-catalog"
GPU = "joyner-gpu-heartbeat"
NOW = 1792585000000


def signed_document(key, publisher=PUBLISHER, seq=1):
    is_gpu = publisher == GPU
    d = {
        "schema": "fleet-publisher-envelope.v1",
        "publisher_id": publisher,
        "node_id": "joyner",
        "role": "gpu_presence" if is_gpu else "model_catalog",
        "key_id": "gpu-key" if is_gpu else "catalog-key",
        "sequence": seq,
        "issued_at_ms": NOW,
        "expires_at_ms": NOW + 30000,
        "payload": (
            {"gpu_canary_guarded": True, "dispatch_admitted": False}
            if is_gpu else {"library": ["joyner-toolcall"], "loaded": []}
        ),
        "signature": "0" * 86,
    }
    d["signature"] = base64.urlsafe_b64encode(
        key.sign(signing_message(d))
    ).decode().rstrip("=")
    return d


def verifier(path, *, keys):
    roots = {
        PUBLISHER: TrustedPublisher(
            PUBLISHER, "joyner", "model_catalog", "catalog-key",
            keys[PUBLISHER].public_key(), ("joyner-toolcall", "joyner-ling"),
        ),
        GPU: TrustedPublisher(
            GPU, "joyner", "gpu_presence", "gpu-key", keys[GPU].public_key(),
        ),
    }
    return SignedFleetPublisherVerifier(roots, fence=LocalSQLiteReplayFence(path))


@pytest.fixture
def keys():
    return {p: Ed25519PrivateKey.generate() for p in (PUBLISHER, GPU)}


def test_sequence_survives_new_verifier_instance(tmp_path, keys):
    path = tmp_path / "research.sqlite3"
    v = verifier(path, keys=keys)
    r = v.verify(signed_document(keys[PUBLISHER], seq=1), now_ms=NOW)
    assert r.production_admission == "NO_GO"
    assert not r.replay_protection_durable
    del v
    new = verifier(path, keys=keys)
    with pytest.raises(PublisherEnvelopeError, match="replay"):
        new.verify(signed_document(keys[PUBLISHER], seq=1), now_ms=NOW)
    new.verify(signed_document(keys[PUBLISHER], seq=2), now_ms=NOW)


def test_two_publishers_have_independent_restart_highwater(tmp_path, keys):
    path=tmp_path / "research.sqlite3"
    a=verifier(path, keys=keys)
    a.verify(signed_document(keys[PUBLISHER], seq=9), now_ms=NOW)
    a.verify(signed_document(keys[GPU], publisher=GPU, seq=1), now_ms=NOW)
    del a
    b=verifier(path, keys=keys)
    with pytest.raises(PublisherEnvelopeError, match="replay"):
        b.verify(signed_document(keys[PUBLISHER], seq=9), now_ms=NOW)
    with pytest.raises(PublisherEnvelopeError, match="replay"):
        b.verify(signed_document(keys[GPU], publisher=GPU, seq=1), now_ms=NOW)
    b.verify(signed_document(keys[PUBLISHER], seq=10), now_ms=NOW)
    b.verify(signed_document(keys[GPU], publisher=GPU, seq=2), now_ms=NOW)


def test_sqlite_store_atomic_for_concurrent_instances(tmp_path):
    path=tmp_path/"concurrent.sqlite3"
    LocalSQLiteReplayFence(path)
    n=12
    gate=threading.Barrier(n)
    def call(_):
        fence=LocalSQLiteReplayFence(path)
        gate.wait(timeout=6)
        try:
            fence.consume(PUBLISHER, 1)
            return "accepted"
        except PublisherEnvelopeError:
            return "replayed"
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        results=list(pool.map(call, range(n)))
    assert results.count("accepted")==1
    assert results.count("replayed")==n-1


def test_subprocess_restart_replay_blocked(tmp_path):
    path=tmp_path/"process.sqlite3"
    LocalSQLiteReplayFence(path).consume(PUBLISHER, 6)
    # A separate process opens the same DB. This never touches router services.
    code=(
        "from auto_router.fleet_publisher_envelope import "
        "LocalSQLiteReplayFence, PublisherEnvelopeError\n"
        "import sys\n"
        "f=LocalSQLiteReplayFence(sys.argv[1])\n"
        "try:\n"
        " f.consume('joyner-model-catalog',6)\n"
        "except PublisherEnvelopeError:\n"
        " print('REPLAY_BLOCKED'); sys.exit(0)\n"
        "sys.exit(9)\n"
    )
    r=subprocess.run(
        [sys.executable, "-c", code, str(path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.strip() == "REPLAY_BLOCKED"


def test_invalid_signature_does_not_advance_local_sqlite(tmp_path, keys):
    path=tmp_path/"research.sqlite3"
    v=verifier(path,keys=keys)
    d=signed_document(keys[GPU], publisher=PUBLISHER, seq=7)
    with pytest.raises(PublisherEnvelopeError, match="signature"):
        v.verify(d, now_ms=NOW)
    r=v.verify(signed_document(keys[PUBLISHER],seq=7), now_ms=NOW)
    assert r.sequence==7


def test_expired_envelope_does_not_advance_local_sqlite(tmp_path, keys):
    path=tmp_path/"research.sqlite3"
    v=verifier(path,keys=keys)
    d=signed_document(keys[PUBLISHER],seq=7)
    with pytest.raises(PublisherEnvelopeError, match="expired"):
        v.verify(d, now_ms=NOW+30000)
    assert v.verify(d, now_ms=NOW).sequence==7


def test_no_relative_path_or_memory_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        LocalSQLiteReplayFence("research.sqlite3")
    with pytest.raises(ValueError, match="absolute"):
        LocalSQLiteReplayFence(":memory:")


def test_disallow_symlink_database(tmp_path):
    path=tmp_path/"store.sqlite3"
    LocalSQLiteReplayFence(path)
    link=tmp_path/"linked.sqlite3"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        LocalSQLiteReplayFence(link)


def test_file_replacement_detected_in_running_process(tmp_path):
    path=tmp_path/"research.sqlite3"
    fence=LocalSQLiteReplayFence(path)
    fence.consume(PUBLISHER,1)
    path.unlink()
    with pytest.raises(PublisherEnvelopeError, match="unavailable"):
        fence.consume(PUBLISHER,2)


def test_corrupt_sqlite_file_fails_closed(tmp_path):
    path=tmp_path/"research.sqlite3"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(PublisherEnvelopeError, match="unavailable"):
        LocalSQLiteReplayFence(path)


def test_db_locked_fails_closed_without_advancing(tmp_path):
    path=tmp_path/"research.sqlite3"
    fence=LocalSQLiteReplayFence(path)
    # Invalid sequence is rejected before any attempt to change disk.
    with pytest.raises(PublisherEnvelopeError):
        fence.consume(PUBLISHER,0)
    fence.consume(PUBLISHER,1)


def test_sqlite_database_rollback_is_not_protected(tmp_path, keys):
    """EXPECTED GAP: snapshots defeat local SQLite; NEVER call it authoritative."""
    path=tmp_path/"research.sqlite3"
    checkpoint=tmp_path/"old-state.backup"
    v=verifier(path, keys=keys)
    v.verify(signed_document(keys[PUBLISHER],seq=1),now_ms=NOW)
    # Copy a coherent closed SQLite DB image; no WAL mode used.
    shutil.copyfile(path, checkpoint)
    v.verify(signed_document(keys[PUBLISHER],seq=2),now_ms=NOW)
    del v
    # Simulate attacker-controlled disk rollback before verifier process restart.
    shutil.copyfile(checkpoint,path)
    rolled=verifier(path,keys=keys)
    accepted=rolled.verify(signed_document(keys[PUBLISHER],seq=2),now_ms=NOW)
    assert accepted.sequence==2
    assert accepted.production_admission=="NO_GO"
    assert not accepted.replay_protection_durable
    assert not accepted.publisher_custody_verified


def test_sqlite_replay_fence_cannot_create_residency_proof(tmp_path, keys):
    v=verifier(tmp_path/"research.sqlite3",keys=keys)
    result=v.verify(signed_document(keys[GPU],publisher=GPU,seq=1),now_ms=NOW)
    assert result.role=="gpu_presence"
    assert result.production_admission=="NO_GO"
    assert not result.gpu_residency_verified
    assert not result.subject_owner_bound


def test_direct_fence_rejects_noncanonical_id(tmp_path):
    fence=LocalSQLiteReplayFence(tmp_path/"research.sqlite3")
    for publisher in ("../etc/passwd", "", False, "x" * 100, "Good"):
        with pytest.raises(PublisherEnvelopeError):
            fence.consume(publisher,1)