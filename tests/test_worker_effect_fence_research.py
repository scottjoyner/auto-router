"""Hermetic safety checks for synthetic worker-side effect acceptance only."""
from __future__ import annotations

from pathlib import Path
import hashlib
import pytest
import sys

RESEARCH=Path(__file__).resolve().parents[1]/"research/fenced_authority"
sys.path.insert(0,str(RESEARCH))
from worker_effect_gate import GuardedSyntheticStream, WorkerEffectDenied
from pgwire_client import PgwireError


def test_synthetic_chunk_accepted_only_after_authority_ack():
    checked=[]
    cancelled=[]
    stream=GuardedSyntheticStream(
        accept_chunk=lambda seq,digest: checked.append((seq,digest)) or True,
        cancel_model=lambda:cancelled.append("stop")
    )
    first=stream.emit(b"first-token")
    second=stream.emit(b"second-token")
    assert first==b"first-token" and second==b"second-token"
    assert checked==[(1,hashlib.sha256(b"first-token").hexdigest()),
                     (2,hashlib.sha256(b"second-token").hexdigest())]
    assert stream.accepted==2 and cancelled==[]


def test_revocation_denial_calls_cancel_once_no_second_output():
    seen=[]
    cancelled=[]
    def authority(seq,digest):
        seen.append(seq)
        return seq==1
    worker=GuardedSyntheticStream(accept_chunk=authority,
                   cancel_model=lambda:cancelled.append("stop"))
    assert worker.emit(b"first")==b"first"
    with pytest.raises(WorkerEffectDenied,match="output blocked"):
        worker.emit(b"second")
    with pytest.raises(WorkerEffectDenied,match="already cancelled"):
        worker.emit(b"third")
    assert seen==[1,2]
    assert worker.accepted==1 and worker.cancelled
    assert cancelled==["stop"]


def test_partition_or_timeout_fails_closed():
    cancelled=[]
    def offline(_seq,_digest):
        raise PgwireError("authority connection refused")
    worker=GuardedSyntheticStream(accept_chunk=offline,
               cancel_model=lambda:cancelled.append(True))
    with pytest.raises(WorkerEffectDenied,match="blocked"):
        worker.emit(b"token")
    assert worker.accepted==0
    assert cancelled==[True]


def test_invalid_output_denies_and_cancels():
    for val in (b"", "string-chunk", bytearray(b"x"), None):
        cancelled=[]
        worker=GuardedSyntheticStream(
            accept_chunk=lambda *_:pytest.fail("authority must not be used"),
            cancel_model=lambda:cancelled.append(True)
        )
        with pytest.raises(WorkerEffectDenied,match="invalid"):
            worker.emit(val)
        assert worker.cancelled and cancelled==[True]


def test_nonboolean_authority_ack_denied():
    for value in (None,1,"1",[],False):
        cancelled=[]
        worker=GuardedSyntheticStream(accept_chunk=lambda *_:value,
                                      cancel_model=lambda:cancelled.append(True))
        with pytest.raises(WorkerEffectDenied):
            worker.emit(b"secret output")
        assert worker.accepted==0 and cancelled==[True]


def test_manual_cancel_denies_new_output_and_does_not_repeat_cleanup():
    cancelled=[]
    worker=GuardedSyntheticStream(
        accept_chunk=lambda *_:True,
        cancel_model=lambda:cancelled.append(True)
    )
    worker.stop()
    worker.stop()
    with pytest.raises(WorkerEffectDenied,match="already cancelled"):
        worker.emit(b"token")
    assert cancelled==[True]


def test_chunk_schema_rejects_nonmonotonic_sequences_and_revoke():
    sql=(RESEARCH/"worker_effect_schema.sql").read_text()
    assert sql.startswith("-- EXPERIMENT ONLY")
    assert "FOR UPDATE" in sql
    assert "scope_hash=p_scope" in sql
    assert "current_scope.state<>'active'" in sql
    assert "current_scope.epoch<>p_epoch" in sql
    assert "current_scope.expires_at<=clock_timestamp()" in sql
    assert "execution.status<>'reserved'" in sql
    assert "p_sequence<>last_seq+1" in sql
    assert "PRIMARY KEY (request_hash, sequence)" in sql
    assert "REVOKE EXECUTE ON FUNCTION fence_research.accept_chunk(" in sql
    assert "REVOKE ALL ON TABLE fence_research.accepted_chunks FROM PUBLIC;" in sql


def test_physical_harness_replays_two_research_scenarios():
    source=(RESEARCH/"qualify_twohost.py").read_text()
    assert 'revoke_worker,first_revoke=start_worker(revoke_scope,1,201)' in source
    assert 'revocation_event=finish_worker(revoke_worker)' in source
    assert 'partition_worker,first_partition=start_worker(partition_scope,1,202)' in source
    assert 'partition_event=finish_worker(partition_worker)' in source
    assert '"synthetic_effect_ack_count_per_worker":1' in source
    assert '"real_model_worker_cancel_proven":False' in source
    assert '"network_delivery_linearizability_proven":False' in source


def test_worker_code_only_synthetic_models_and_sqlite_no_live():
    source=(RESEARCH/"worker_effect_trial.py").read_text()
    assert 'worker.emit(b"synthetic-first-chunk")' in source
    assert 'worker.emit(b"synthetic-second-chunk")' in source
    assert '"NEXT\\n"' in source
    assert "ssh" not in (RESEARCH/"worker_effect_gate.py").read_text().lower()
    assert "v1/chat/completions" not in source
    assert "llama-server" not in source


def test_full_test_has_no_prod_workers_or_service_changes():
    source=(RESEARCH/"qualify_twohost.py").read_text()
    assert '"--publish",f"127.0.0.1:{HOST_PORT}:5432"' in source
    assert "POSTGRES_HOST_AUTH_METHOD=trust" in source  # disposable only
    assert "if not args.run:" in source
    assert "tunnel.terminate()" in source
    assert '["docker","rm","-f",CONTAINER]' in source
