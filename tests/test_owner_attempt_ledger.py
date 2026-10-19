"""Durable, owner-bound predispatch attempt proof across process boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from auto_router.owner_attempt_ledger import (
    DurableOwnerAttemptLedger, OwnerAttemptDenied,
    OwnerAttemptStoreError, acquire_strict_attempt,
)


def claim(*,owner="person-a",jti="unique-jti",max_attempts=2,expires=None):
    return {
        "owner_issuer":"test-idp","owner_subject":owner,
        "task_id":"task-alpha","claim_id":"claim-alpha",
        "agent_id":"agent-1","projection_generation":29,
        "jti":jti,"max_attempts":max_attempts,
        "exp":int(time.time())+180 if expires is None else expires,
    }


def test_strict_attempt_reservation_survives_new_instance(tmp_path):
    path=str(tmp_path/"attempts.sqlite")
    signed=claim()
    assert DurableOwnerAttemptLedger(path).acquire(signed)==1
    assert DurableOwnerAttemptLedger(path).count(signed)==1
    assert DurableOwnerAttemptLedger(path).acquire(signed)==2
    with pytest.raises(OwnerAttemptDenied,match="exhausted"):
        DurableOwnerAttemptLedger(path).acquire(signed)


def test_attempts_reject_cross_owner_reuse_of_signed_token_jti(tmp_path):
    path=str(tmp_path/"attempts.sqlite")
    signed=claim()
    DurableOwnerAttemptLedger(path).acquire(signed)
    with pytest.raises(OwnerAttemptDenied,match="different owner"):
        DurableOwnerAttemptLedger(path).acquire({**signed,"owner_subject":"person-b"})


@pytest.mark.parametrize("different", [
    {"claim_id":"another-claim"},
    {"agent_id":"different-agent"},
    {"task_id":"different-task"},
    {"projection_generation":30},
    {"max_attempts":7},
])
def test_same_jti_changed_claim_denied(tmp_path,different):
    path=str(tmp_path/"attempts.sqlite")
    signed=claim()
    DurableOwnerAttemptLedger(path).acquire(signed)
    with pytest.raises(OwnerAttemptDenied,match="different owner or claim"):
        DurableOwnerAttemptLedger(path).acquire({**signed,**different})


def test_two_distinct_owners_no_shared_attempt_budget(tmp_path):
    store=DurableOwnerAttemptLedger(str(tmp_path/"attempts.sqlite"))
    a=claim(owner="alpha",jti="jti-alpha",max_attempts=1)
    b=claim(owner="beta",jti="jti-beta",max_attempts=1)
    assert store.acquire(a)==1
    assert store.acquire(b)==1
    with pytest.raises(OwnerAttemptDenied):
        store.acquire(a)
    with pytest.raises(OwnerAttemptDenied):
        store.acquire(b)


def test_parallel_sqlite_transactions_do_not_overadmit(tmp_path):
    path=str(tmp_path/"attempts.sqlite")
    base=claim(max_attempts=7)
    def attempt(_index):
        try:return DurableOwnerAttemptLedger(path).acquire(base)
        except OwnerAttemptDenied:return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        numbers=list(pool.map(attempt,range(24)))
    assert sorted(n for n in numbers if n is not None)==list(range(1,8))
    assert numbers.count(None)==17


def test_cross_process_restart_cannot_reset_counter(tmp_path):
    path=str(tmp_path/"attempts.sqlite")
    signed=claim(max_attempts=1)
    assert DurableOwnerAttemptLedger(path).acquire(signed)==1
    import json
    code=(
        "import json,sys;"
        "from auto_router.owner_attempt_ledger import DurableOwnerAttemptLedger;"
        "print(DurableOwnerAttemptLedger(sys.argv[1]).count(json.loads(sys.argv[2])))"
    )
    env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1]/"src")}
    res=subprocess.run([sys.executable,"-c",code,path,json.dumps(signed)],
                      env=env,capture_output=True,text=True,timeout=8,check=True)
    assert res.stdout.strip()=="1"
    with pytest.raises(OwnerAttemptDenied):
        DurableOwnerAttemptLedger(path).acquire(signed)


def test_no_fallback_without_configured_store(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_OWNER_ATTEMPT_DB_PATH",raising=False)
    with pytest.raises(OwnerAttemptStoreError,match="configured"):
        acquire_strict_attempt(claim())


def test_private_store_created_0600(tmp_path):
    path=tmp_path/"attempts.sqlite"
    DurableOwnerAttemptLedger(str(path))
    assert path.stat().st_mode & 0o777 == 0o600


def test_world_readable_existing_database_denied(tmp_path):
    path=tmp_path/"attempts.sqlite"
    path.write_bytes(b"")
    path.chmod(0o644)
    with pytest.raises(OwnerAttemptStoreError,match="unsafe"):
        DurableOwnerAttemptLedger(str(path))


def test_world_accessible_parent_denied(tmp_path):
    parent=tmp_path/"public"
    parent.mkdir(mode=0o755)
    with pytest.raises(OwnerAttemptStoreError,match="0700"):
        DurableOwnerAttemptLedger(str(parent/"attempts.sqlite"))


def test_symlinked_database_denied(tmp_path):
    innocent=tmp_path/"innocent"
    innocent.write_text("keep")
    target=tmp_path/"attempts.sqlite"
    target.symlink_to(innocent)
    with pytest.raises(OwnerAttemptStoreError):
        DurableOwnerAttemptLedger(str(target))
    assert innocent.read_text()=="keep"


def test_in_memory_and_relative_paths_denied():
    for path in (":memory:","file::memory:","relative.db",""):
        with pytest.raises(OwnerAttemptStoreError):
            DurableOwnerAttemptLedger(path)


def test_unsigned_legacy_owner_denied(tmp_path):
    ledger=DurableOwnerAttemptLedger(str(tmp_path/"attempts.sqlite"))
    signed=claim()
    del signed["owner_subject"]
    with pytest.raises(OwnerAttemptDenied,match="absent"):
        ledger.acquire(signed)


@pytest.mark.parametrize("replacement",[True,"",123,None])
def test_malformed_signed_jti_denied(tmp_path,replacement):
    ledger=DurableOwnerAttemptLedger(str(tmp_path/"attempts.sqlite"))
    with pytest.raises(OwnerAttemptDenied):
        ledger.acquire({**claim(),"jti":replacement})


@pytest.mark.parametrize("maximum",[0,101,True,"5",None])
def test_missing_or_invalid_max_attempts_denied(tmp_path,maximum):
    ledger=DurableOwnerAttemptLedger(str(tmp_path/"attempts.sqlite"))
    with pytest.raises(OwnerAttemptDenied,match="budget"):
        ledger.acquire({**claim(),"max_attempts":maximum})


def test_expired_token_denied(tmp_path):
    ledger=DurableOwnerAttemptLedger(str(tmp_path/"attempts.sqlite"))
    with pytest.raises(OwnerAttemptDenied,match="expired"):
        ledger.acquire(claim(expires=int(time.time())-1))


def test_excessively_long_lived_token_denied(tmp_path):
    ledger=DurableOwnerAttemptLedger(str(tmp_path/"attempts.sqlite"))
    with pytest.raises(OwnerAttemptDenied,match="bounded validity"):
        ledger.acquire(claim(expires=int(time.time())+200000))


def test_sqlite_storage_failure_denies(monkeypatch,tmp_path):
    ledger=DurableOwnerAttemptLedger(str(tmp_path/"attempts.sqlite"))
    def broken():
        raise OwnerAttemptStoreError("unavailable")
    monkeypatch.setattr(ledger,"_connect",broken)
    with pytest.raises(OwnerAttemptStoreError):
        ledger.acquire(claim())


def test_no_raw_owner_strings_or_jti_persisted(tmp_path):
    path=str(tmp_path/"attempts.sqlite")
    signed=claim(owner="test-very-sensitive-owner-subject",jti="never-plaintext-jti")
    DurableOwnerAttemptLedger(path).acquire(signed)
    data=Path(path).read_bytes()
    assert b"test-very-sensitive-owner-subject" not in data
    assert b"never-plaintext-jti" not in data
    with sqlite3.connect(path) as conn:
        record=conn.execute("SELECT jti_hash,binding_hash FROM signed_owner_attempts").fetchone()
        assert len(record[0])==len(record[1])==64
