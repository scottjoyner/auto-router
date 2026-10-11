"""P0 #37 durable root ownership: restart, concurrency and fail-closed tests.

All DBs, signing keys, and process executions are disposable pytest fixtures;
no production router, gateway, NAS, or trace sink is contacted.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_p0_37_verified_user_delegation import _token

from auto_router.user_delegation import verify_user_delegation
from auto_router.user_root_ledger import (
    RootOwnerConflict,
    RootOwnerLedger,
    RootOwnerStorageError,
    read_pinned_root_secret,
)

_SECRET = b"\xb7" * 32


def _ledger(path, *, secret=_SECRET):
    # Test-only explicit provisioning; production constructor refuses an
    # unknown/missing database and NEVER automatically recreates it.
    return RootOwnerLedger(path, secret, bootstrap=not path.exists())


def _claim(signer, subject, root, attempt, *, model="auto/code"):
    token = _token(signer, subject, root=root, attempt=attempt, model=model)
    return verify_user_delegation(
        token, public_key=signer.public_key(), key_id="fixture-human-key-1",
        model=model,
    ), token


def _uuid():
    import uuid
    return str(uuid.uuid4())


def test_reopen_rejects_same_root_new_human_and_keeps_old_attempt(tmp_path):
    file = tmp_path / "bound.sqlite"
    signer = Ed25519PrivateKey.generate()
    root = _uuid()
    a, a_token = _claim(signer, "alice", root, _uuid())
    assert _ledger(file).reserve(a, a_token) is True

    # Simulated restart: close initial SQLite connection and reopen.
    new_ledger = _ledger(file)
    b, b_token = _claim(signer, "bob", root, _uuid())
    with pytest.raises(RootOwnerConflict, match="root owner"):
        new_ledger.reserve(b, b_token)
    assert new_ledger.reserve(a, a_token) is False
    a2, a2_token = _claim(signer, "alice", root, _uuid())
    assert new_ledger.reserve(a2, a2_token) is True
    with sqlite3.connect(file) as con:
        roots = con.execute("SELECT COUNT(*) FROM p0_root_owner").fetchone()[0]
        attempts = con.execute("SELECT COUNT(*) FROM p0_attempt_owner").fetchone()[0]
    assert (roots, attempts) == (1, 2)


def test_one_attempt_cannot_be_reassigned_to_another_root(tmp_path):
    signer = Ed25519PrivateKey.generate()
    attempt = _uuid()
    first, first_token = _claim(signer, "alice", _uuid(), attempt)
    other, other_token = _claim(signer, "alice", _uuid(), attempt)
    ledger = _ledger(tmp_path / "attempt.sqlite")
    assert ledger.reserve(first, first_token)
    with pytest.raises(RootOwnerConflict, match="attempt was already"):
        ledger.reserve(other, other_token)


def test_root_cannot_switch_model_under_same_subject(tmp_path):
    signer = Ed25519PrivateKey.generate()
    root = _uuid()
    first, t1 = _claim(signer, "alice", root, _uuid())
    second, t2 = _claim(signer, "alice", root, _uuid(), model="auto/review")
    ledger = _ledger(tmp_path / "model.sqlite")
    assert ledger.reserve(first, t1)
    with pytest.raises(RootOwnerConflict, match="root owner"):
        ledger.reserve(second, t2)


def test_same_attempt_different_signed_jti_cannot_overwrite(tmp_path):
    signer = Ed25519PrivateKey.generate()
    root, attempt = _uuid(), _uuid()
    first, t1 = _claim(signer, "alice", root, attempt)
    second, t2 = _claim(signer, "alice", root, attempt)
    ledger = _ledger(tmp_path / "jti.sqlite")
    assert ledger.reserve(first, t1)
    with pytest.raises(RootOwnerConflict, match="attempt was already"):
        ledger.reserve(second, t2)


def test_concurrent_same_root_different_owner_has_one_winner(tmp_path):
    signer = Ed25519PrivateKey.generate()
    file = tmp_path / "concurrent.sqlite"
    root = _uuid()
    a = _claim(signer, "alice", root, _uuid())
    b = _claim(signer, "bob", root, _uuid())
    barrier = Barrier(2)
    first = _ledger(file)

    def worker(item):
        ledger = _ledger(file)
        barrier.wait(timeout=5)
        try:
            return ledger.reserve(*item)
        except RootOwnerConflict:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (a, b)))
    assert sorted(str(result) for result in results) == ["True", "denied"]
    with sqlite3.connect(file) as db:
        assert db.execute("SELECT COUNT(*) FROM p0_root_owner").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM p0_attempt_owner").fetchone()[0] == 1
    winner = a if results[0] is True else b
    assert first.reserve(*winner) is False


def test_concurrent_distinct_roots_from_two_owners_both_commit(tmp_path):
    signer = Ed25519PrivateKey.generate()
    file = tmp_path / "parallel.sqlite"
    a = _claim(signer, "alice", _uuid(), _uuid())
    b = _claim(signer, "bob", _uuid(), _uuid())
    barrier = Barrier(2)
    _ledger(file)

    def worker(item):
        local = _ledger(file)
        barrier.wait(timeout=5)
        return local.reserve(*item)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(worker, (a, b))) == [True, True]
    with sqlite3.connect(file) as db:
        assert db.execute("SELECT COUNT(*) FROM p0_root_owner").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM p0_attempt_owner").fetchone()[0] == 2


def test_new_python_process_cannot_reassign_previous_process_root(tmp_path):
    signer = Ed25519PrivateKey.generate()
    file = tmp_path / "subprocess.sqlite"
    root = _uuid()
    alice, token_a = _claim(signer, "alice", root, _uuid())
    bob, token_b = _claim(signer, "bob", root, _uuid())
    _ledger(file).reserve(alice, token_a)
    # Pass only disposable fixture claims to a separate Python process.
    # The actual persisted store uses HMAC opaque hashes, not raw subject IDs.
    import json
    program = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from auto_router.user_root_ledger import RootOwnerLedger,RootOwnerConflict\n"
        "ledger=RootOwnerLedger(Path(sys.argv[1]),bytes.fromhex(sys.argv[2]))\n"
        "payload=json.loads(sys.argv[3])\n"
        "try:\n"
        "  ledger.reserve(payload['claims'],payload['token'])\n"
        "except RootOwnerConflict:\n"
        "  print('DENIED')\n"
        "else:\n"
        "  print('UNEXPECTED_ACCEPT')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(file), _SECRET.hex(),
         json.dumps({"claims": bob, "token": token_b})],
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert result.stdout.strip() == "DENIED"
    assert _ledger(file).reserve(alice, token_a) is False


def test_key_rotation_without_verified_migration_fails_closed(tmp_path):
    file = tmp_path / "key-bound.sqlite"
    signer = Ed25519PrivateKey.generate()
    row, token = _claim(signer, "alice", _uuid(), _uuid())
    assert _ledger(file).reserve(row, token)
    with pytest.raises(RootOwnerStorageError, match="key mismatch"):
        _ledger(file, secret=b"\xa8" * 32)
    assert _ledger(file).reserve(row, token) is False


def test_existing_db_removal_must_not_reinitialize_undetected(tmp_path):
    # This checks startup metadata integrity, not a malicious DB replacement
    # or missing-volume detection across a distributed router cluster.
    file = tmp_path / "metadata.sqlite"
    signer = Ed25519PrivateKey.generate()
    row, token = _claim(signer, "alice", _uuid(), _uuid())
    ledger = _ledger(file)
    assert ledger.reserve(row, token)
    with sqlite3.connect(file) as db:
        db.execute("DELETE FROM p0_owner_key_check")
    with pytest.raises(RootOwnerStorageError, match="metadata absent"):
        _ledger(file)


def test_database_never_stores_plaintext_subject_or_token(tmp_path):
    file = tmp_path / "privacy.sqlite"
    signer = Ed25519PrivateKey.generate()
    root, attempt = _uuid(), _uuid()
    owner, signed = _claim(signer, "alice", root, attempt)
    ledger = _ledger(file)
    assert ledger.reserve(owner, signed)
    with sqlite3.connect(file) as db:
        values = db.execute(
            "SELECT root_hash, owner_hash, model_hash FROM p0_root_owner"
        ).fetchall()
        attrs = db.execute(
            "SELECT attempt_hash, root_hash, signer_jti_hash, token_hash "
            "FROM p0_attempt_owner"
        ).fetchall()
    all_values = str(values + attrs)
    assert len(values) == len(attrs) == 1
    assert all(len(piece) == 64 for piece in (*values[0], *attrs[0]))
    for secret in ("alice", "same-tenant", signed, root, attempt, "auto/code"):
        assert secret not in all_values


def test_symlink_and_insecure_owner_store_are_rejected(tmp_path):
    real = tmp_path / "original.sqlite"
    _ledger(real)
    symlink = tmp_path / "alias.sqlite"
    symlink.symlink_to(real)
    with pytest.raises(RootOwnerStorageError, match="symlink"):
        _ledger(symlink)
    real.chmod(0o644)
    with pytest.raises(RootOwnerStorageError, match="unsafe"):
        _ledger(real)


def test_secret_file_requires_exact_bytes_safe_mode_and_owner(tmp_path):
    key = tmp_path / "secret.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    assert len(read_pinned_root_secret(str(key))) == 32
    key.chmod(0o644)
    with pytest.raises(RootOwnerStorageError, match="permissions"):
        read_pinned_root_secret(str(key))
    key.chmod(0o600)
    key.write_bytes(b"not-long-enough")
    with pytest.raises(RootOwnerStorageError, match="exactly 32"):
        read_pinned_root_secret(str(key))


def test_storage_unavailable_denies_before_any_scope_grant(tmp_path):
    with pytest.raises(RootOwnerStorageError, match="parent directory"):
        _ledger(tmp_path / "does-not-exist" / "owner.sqlite")


def test_lost_owner_database_cannot_silently_bootstrap_after_restart(tmp_path):
    file = tmp_path / "lost.sqlite"
    signer = Ed25519PrivateKey.generate()
    row, token = _claim(signer, "alice", _uuid(), _uuid())
    assert _ledger(file).reserve(row, token)
    file.unlink()  # Disposable test file ONLY, never production custody.
    with pytest.raises(RootOwnerStorageError, match="explicit provisioning"):
        RootOwnerLedger(file, _SECRET)


def test_replacing_owner_database_midrun_is_rejected(tmp_path):
    file = tmp_path / "running.sqlite"
    signer = Ed25519PrivateKey.generate()
    row, token = _claim(signer, "alice", _uuid(), _uuid())
    ledger = _ledger(file)
    assert ledger.reserve(row, token)
    replacement = tmp_path / "new.sqlite"
    _ledger(replacement)
    os.replace(replacement, file)  # Local tmp fixture, not live root storage.
    new_row, new_token = _claim(signer, "bob", _uuid(), _uuid())
    with pytest.raises(RootOwnerStorageError, match="storage identity changed"):
        ledger.reserve(new_row, new_token)


def test_startup_never_initializes_an_empty_existing_db_file(tmp_path):
    file = tmp_path / "empty.sqlite"
    file.write_bytes(b"")
    file.chmod(0o600)
    with pytest.raises(RootOwnerStorageError, match="request-bound schema unavailable"):
        RootOwnerLedger(file, _SECRET)


def test_missing_identity_metadata_denies_new_reservations(tmp_path):
    file = tmp_path / "metadata-running.sqlite"
    signer = Ed25519PrivateKey.generate()
    row, token = _claim(signer, "alice", _uuid(), _uuid())
    ledger = _ledger(file)
    assert ledger.reserve(row, token)
    with sqlite3.connect(file) as db:
        db.execute("DELETE FROM p0_owner_key_check")
    new_row, new_token = _claim(signer, "bob", _uuid(), _uuid())
    with pytest.raises(RootOwnerStorageError, match="key mismatch"):
        ledger.reserve(new_row, new_token)


def test_gateway_jti_cannot_be_reused_for_another_signed_attempt(tmp_path):
    signer = Ed25519PrivateKey.generate()
    shared_jti = "same-jti-deliberately-reused"
    first_token = _token(
        signer, "alice", claims_changes={"jti": shared_jti}
    )
    second_token = _token(
        signer, "bob", claims_changes={"jti": shared_jti}
    )
    first = verify_user_delegation(
        first_token, public_key=signer.public_key(),
        key_id="fixture-human-key-1", model="auto/code",
    )
    second = verify_user_delegation(
        second_token, public_key=signer.public_key(),
        key_id="fixture-human-key-1", model="auto/code",
    )
    ledger = _ledger(tmp_path / "jti-global.sqlite")
    assert ledger.reserve(first, first_token) is True
    with pytest.raises(RootOwnerConflict, match="signing jti"):
        ledger.reserve(second, second_token)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT COUNT(*) FROM p0_root_owner").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM p0_attempt_owner").fetchone()[0] == 1


def test_independent_unreconciled_node_databases_cannot_prove_global_owner(
    tmp_path,
):
    """Explicit negative control: this is why multi-node admission is NOT ready.

    Two disconnected nodes using separate DBs cannot enforce one global root.
    A deployed fleet must use one linearizable owner authority and proof that
    all writers use it. This passing NEGATIVE test is NOT an acceptance proof.
    """
    signer = Ed25519PrivateKey.generate()
    same_root = _uuid()
    a, a_token = _claim(signer, "alice", same_root, _uuid())
    b, b_token = _claim(signer, "bob", same_root, _uuid())
    isolated_a = _ledger(tmp_path / "node-a.sqlite")
    isolated_b = _ledger(tmp_path / "node-b.sqlite")
    assert isolated_a.reserve(a, a_token) is True
    assert isolated_b.reserve(b, b_token) is True
    # Deliberately demonstrate split-brain admission is possible in two
    # independent stores. Never infer cross-node uniqueness from this ledger.
