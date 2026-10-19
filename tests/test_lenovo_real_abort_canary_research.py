"""Reproducibility and fail-closed guards for research-only Lenovo canaries."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]/"research/runtime_abort"
BASIC=ROOT/"lenovo_loopback_abort.py"
REMOTE=ROOT/"lenovo_remote_canary_host.py"
DRIVER=ROOT/"qualify_lenovo_router_stream.py"


def test_loopback_canary_approved_exact_host_and_model():
    source=BASIC.read_text()
    assert 'HOST="scott-Lenovo-ideapad-330S-15IKB"' in source
    assert 'PORT=12742' in source
    assert 'LIVE_PORT=1234' in source
    assert 'LFM2.5-1.2B-Instruct-Q8_0.gguf' in source
    assert '"--host","127.0.0.1"' in source
    assert '"--gpu-layers","0"' in source
    assert '"--threads","2"' in source
    assert '"--parallel","1"' in source


def test_nonproduction_lenovo_direct_canary_resources_and_cleanup():
    s=BASIC.read_text()
    assert 'memory_full_avg10' in s
    assert 'metrics["available_gib"]<5.5' in s
    assert 'metrics["memory_full_avg10"]>0.05' in s
    assert 'resource.setrlimit(resource.RLIMIT_AS' in s
    assert 'os.nice(14)' in s
    assert 'start_new_session=True' in s
    assert 'os.killpg(child.pid,signal.SIGTERM)' in s
    assert 'os.killpg(child.pid,signal.SIGKILL)' in s
    assert 'child.wait(timeout=4)' in s


def test_lenovo_prod_inference_listener_identity_and_health_protected():
    s=BASIC.read_text()
    assert 'unit.get("ActiveState")!="active"' in s
    assert 'f"http://127.0.0.1:{LIVE_PORT}/health"' in s
    assert 'actual_start!=live_start' in s
    assert '"production_service_pid_starttime_unchanged":True' in s


def test_direct_canary_has_no_public_interface_or_persistent_model_side_effect():
    s=BASIC.read_text()
    assert '"--host","0.0.0.0"' not in s
    assert '"--host","127.0.0.1"' in s
    assert 'TemporaryDirectory(' in s
    assert 'MODEL.write_' not in s
    assert 'systemctl --user restart' not in s
    assert 'requests.post(f"http://127.0.0.1:{PORT}/completion"' in s


def test_remote_canary_does_not_write_to_lenovo_home():
    s=REMOTE.read_text()
    assert 'TemporaryDirectory(' in s
    assert 'remote controller deadline' in s
    assert 'sys.stdin.readline().strip()!="STOP"' in s
    assert '"live_process_identity_unchanged":same_pid' in s
    assert 'os.killpg(child.pid,signal.SIGTERM)' in s
    assert 'open("/home/scott' not in s
    assert 'pip install' not in s


def test_router_driver_is_plan_only_without_explicit_run():
    p=subprocess.run([sys.executable,str(DRIVER)],
                     capture_output=True,text=True,timeout=5,check=True)
    assert "PLAN_ONLY" in p.stdout


def test_router_cross_host_uses_pinned_ssh_and_local_forward():
    s=DRIVER.read_text()
    assert '"StrictHostKeyChecking=yes"' in s
    assert '"BatchMode=yes"' in s
    assert '"-L",f"127.0.0.1:{PORT}:127.0.0.1:12742"' in s
    assert 'PORT=12743' in s
    assert '127.0.0.1:{PORT}/v1' in s
    assert 'temporary_remote_command()' in s


def test_router_synthetic_authority_must_suppress_chunk_four():
    s=DRIVER.read_text()
    assert 'return sequence<4' in s
    assert 'authority.calls!=[1,2,3,4]' in s
    assert 'len(released)!=3' in s
    assert 'authority.runtime_busy_at_revoke is not True' in s
    assert '"possibly_accepted"' in s
    assert 'independently_authenticated_human_principals' in s


def test_harness_requires_privately_confirmed_cleanup():
    s=DRIVER.read_text()
    assert 'ready_confirmed=True' in s
    assert 'cleanup is None' in s
    assert 'cleanup.get("isolated_process_terminated") is not True' in s
    assert 'cleanup.get("live_process_identity_unchanged") is not True' in s
    assert 'cleanup.get("live_health_200") is not True' in s
    assert 'remote.returncode != 0' in s
    assert 'tunnel.terminate()' in s
    assert '"STOP\\n"' in s
    assert '"--run",action="store_true"' in s


def test_real_physical_evidence_proves_two_cross_host_suppression_runs():
    first=json.loads((ROOT/"evidence/x1_router_lenovo_real_fence_20261019.json").read_text())
    second=json.loads((ROOT/"evidence/x1_router_lenovo_real_fence_revalidation_20261019.json").read_text())
    for result in (first,second):
        assert result["decision"]=="PASS_PHYSICAL_X1_ROUTER_LENOVO_REAL_MODEL_STREAM_ABORT_NOT_PRODUCTION"
        assert result["authorized_output_chunks"]==3
        assert result["suppressed_real_output_chunk_four"] is True
        assert result["real_lenovo_model_busy_when_chunk_four_denied"] is True
        assert result["synthetic_abort_calls"]==1
        assert result["terminal_state"]=="possibly_accepted"
        assert result["followup_real_model_generation_success"] is True
        assert result["port_public"]==0
        assert result["temporary_resources_cleaned"] is True
        assert result["real_remote_epoch_authority"] is False


def test_physical_lenovo_direct_healthy_live_service_evidence():
    evidence=json.loads((ROOT/"evidence/lenovo_lfm_real_stream_abort_20261019.json").read_text())
    assert evidence["decision"]=="PASS_ISOLATED_LENOVO_REAL_CPU_STREAM_CLOSE_NOT_EPOCH_GO"
    assert evidence["production_service_pid_starttime_unchanged"] is True
    assert evidence["production_service_health_200_after"] is True
    assert evidence["stream_events_before_http_abort"]==3
    assert evidence["slot_idle_after_abort_s"]<5


def test_bounded_support_source_never_targets_live_inference_for_generation():
    s=BASIC.read_text()
    assert 'f"http://127.0.0.1:{LIVE_PORT}/health"' in s
    assert 'f"http://127.0.0.1:{LIVE_PORT}/completion"' not in s


def test_router_missing_outcome_does_not_claim_real_abort_proven():
    s=DRIVER.read_text()
    assert '"real_remote_epoch_authority":False' in s
    assert '"model_process_internal_abort_proven":False' in s
    assert '"durable_epoch_broker_involved":False' in s
