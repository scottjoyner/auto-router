"""Hermetic safeguards for opt-in real llama-server HTTP cancellation experiment.

Never creates a server unless a human explicitly runs the separate
research script with --run. These tests only inspect and mock guards.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SRC=Path(__file__).resolve().parents[1]/"research/runtime_abort/qualify_llama_abort.py"


@pytest.fixture
def canary():
    spec=importlib.util.spec_from_file_location("isolated_llama_abort_canary",SRC)
    assert spec and spec.loader
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_side_effects_without_explicit_run():
    result=subprocess.run([sys.executable,str(SRC)],capture_output=True,
                          text=True,timeout=8,check=True)
    assert "PLAN_ONLY" in result.stdout
    assert "isolated llama canary requires explicit --run" in result.stdout


def test_canary_host_preflight_rejects_other_hosts(monkeypatch,canary):
    monkeypatch.setattr(canary.socket,"gethostname",lambda:"xwing")
    with pytest.raises(RuntimeError,match="host mismatch"):
        canary._check_preflight()


def test_canary_missing_model_denies(monkeypatch,canary,tmp_path):
    monkeypatch.setattr(canary.MODEL.__class__,"is_file",lambda self:False)
    with pytest.raises(RuntimeError,match="approved binary or model missing"):
        canary._check_preflight()


@pytest.mark.parametrize("available,pressure",[(5,0),(10,0), (60,.8)])
def test_low_memory_or_high_psi_denies(monkeypatch,canary,available,pressure):
    monkeypatch.setattr(canary,"_resident_resources",lambda:{
        "memory_available_gib":available,"memory_full_avg10":pressure,
        "swap_free_gib":4
    })
    with pytest.raises(RuntimeError,match="PSI gate"):
        canary._check_preflight()


def test_bad_model_size_denies(monkeypatch,canary):
    class FakeSize:
        st_size=3_100_000_000
    class FakeModel:
        def is_file(self):return True
        def stat(self):return FakeSize()
    monkeypatch.setattr(canary,"MODEL",FakeModel())
    with pytest.raises(RuntimeError,match="model size"):
        canary._check_preflight()


def test_dedicated_loopback_and_cpu_only_stable():
    source=SRC.read_text()
    assert 'PORT=12741' in source
    assert '"--host","127.0.0.1"' in source
    assert '"--gpu-layers","0"' in source
    assert '"--parallel","1"' in source
    assert '"--threads","2"' in source
    assert '"--ctx-size","2048"' in source
    assert "OMP_NUM_THREADS" in source
    assert 'resource.setrlimit(resource.RLIMIT_AS' in source
    assert 'os.nice(10)' in source


def test_only_created_canary_process_group_terminated():
    source=SRC.read_text()
    assert "start_new_session=True" in source
    assert 'os.killpg(server.pid,signal.SIGTERM)' in source
    assert 'os.killpg(server.pid,signal.SIGKILL)' in source
    assert "server.wait(timeout=7)" in source
    assert "server.wait(timeout=5)" in source
    assert '"process_terminated_on_exit":True' in source


def test_real_provider_wrapped_with_same_stream_effect_gate():
    source=SRC.read_text()
    assert 'provider.stream_completions' in source
    assert 'module._dispatch_stream' in source
    assert 'module.state.stream_effect_authority=authority' in source
    assert 'self.sequences.append(sequence)' in source
    assert 'return sequence<=3' in source
    assert 'if authority.slot_busy_when_revoked is not True:' in source
    assert 'len(accepted)!=3 or authority.sequences!=[1,2,3,4]' in source


def test_interrupted_router_cannot_be_marked_completed():
    source=SRC.read_text()
    assert 'outbox.events[-1].event_type!="router.stream.failed"' in source
    assert '"router_final_state":"possibly_accepted"' in source
    assert '"trusted_epoch_authority_in_test":False' in source
    assert '"real_worker_stop_proof":False' in source


def test_recovery_proof_checks_server_slot_and_followup():
    source=SRC.read_text()
    assert '_wait_idle(client,15)' in source
    assert '"server_slot_idle_after_synthetic_epoch_revoke_s"' in source
    assert '"followup_inference_responded":True' in source
    assert '"/slots"' in source


def test_evidence_written_privately_only_when_opted_in():
    source=SRC.read_text()
    assert 'if not args.run:' in source
    assert "os.O_EXCL|os.O_NOFOLLOW,0o600" in source
    assert "path.is_symlink()" in source
    assert '"no_llm_user_data":True' in source


def test_canary_does_not_target_production_lenovo():
    source=SRC.read_text()
    assert "lenovo" not in source.lower()
    assert "http://127.0.0.1" in source
    assert '--host","0.0.0.0"' not in source


def test_busy_slots_detect_runtime_processing(canary):
    assert canary._busy([{"is_processing":True}])
    assert not canary._busy([{"is_processing":False}])
    assert not canary._busy([{"other":True}])



def test_missing_router_dependency_denies_before_model_start(monkeypatch,canary):
    import importlib.util
    real=importlib.util.find_spec
    monkeypatch.setattr(importlib.util,"find_spec",
                        lambda name: None if name=="pydantic_settings" else real(name))
    with pytest.raises(RuntimeError,match="missing pydantic_settings"):
        canary._check_preflight()
