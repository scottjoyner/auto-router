"""Offline regression checks for bounded, non-authoritative Lenovo witness."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observer = load("temporary_offhost_observer")
qualifier = load("qualify_offhost_fleet_checkpoint")
CHALLENGE = "e3d9ce7a999f4d22b001102030405060"


@pytest.fixture
def transcript():
    return observer.produce_transcript(CHALLENGE)


def test_synthetic_cross_process_checkpoints_are_strictly_no_go(transcript):
    assert qualifier.verify_transcript(transcript, challenge=CHALLENGE)
    result = qualifier.check_remote_evidence(
        transcript, challenge=CHALLENGE,
        now_ms=transcript["latest_checkpoint"]["issued_at_ms"] + 1000,
    )
    assert result["physical_remote_test"] is True
    assert result["production_admission"] == "NO_GO"
    assert result["independent_admin_custody_verified"] is False
    assert result["remote_key_trust"] == "SELF_PRESENTED_SSH_HANDOFF_ONLY"
    assert result["local_db_rollback_detected_with_current_remote_checkpoint"]
    assert result["combined_generation_anchor_rollback_unprevented"]
    assert not result["external_authority_verified"]
    assert not result["model_gpu_residency_verified"]


def test_signed_challenge_is_checked(transcript):
    with pytest.raises(qualifier.OffhostWitnessError, match="claim"):
        qualifier.verify_transcript(
            transcript, challenge="f" * 32,
        )


def test_forged_proof_rejected(transcript):
    t = dict(transcript)
    t["challenge_proof"] = "A" * 86
    with pytest.raises(qualifier.OffhostWitnessError):
        qualifier.verify_transcript(t, challenge=CHALLENGE)


def test_modified_checkpoint_is_rejected_by_challenge_binding(transcript):
    t = dict(transcript)
    t["latest_checkpoint"] = dict(t["latest_checkpoint"])
    t["latest_checkpoint"]["issued_at_ms"] += 1
    with pytest.raises(qualifier.OffhostWitnessError):
        qualifier.verify_transcript(t, challenge=CHALLENGE)


def test_old_generation_cannot_replace_latest_checkpoint(transcript):
    t = dict(transcript)
    t["latest_checkpoint"] = t["old_checkpoint"]
    with pytest.raises(qualifier.OffhostWitnessError, match="CAS"):
        qualifier.verify_transcript(t, challenge=CHALLENGE)


def test_transcript_requires_explicit_no_go(transcript):
    t = dict(transcript)
    t["production_admission"] = "GO"
    with pytest.raises(qualifier.OffhostWitnessError, match="claim"):
        qualifier.verify_transcript(t, challenge=CHALLENGE)


def test_false_independence_claim_rejected(transcript):
    t = dict(transcript)
    t["separate_custody_verified"] = True
    with pytest.raises(qualifier.OffhostWitnessError, match="claim"):
        qualifier.verify_transcript(t, challenge=CHALLENGE)


def test_extra_leaked_fields_rejected(transcript):
    t = dict(transcript)
    t["private_key"] = "FORBIDDEN"
    with pytest.raises(qualifier.OffhostWitnessError, match="shape"):
        qualifier.verify_transcript(t, challenge=CHALLENGE)


def test_missing_or_invalid_public_key_rejected(transcript):
    t = dict(transcript)
    t["observer_public_key"] = "!" * 43
    with pytest.raises(qualifier.OffhostWitnessError, match="encoding"):
        qualifier.verify_transcript(t, challenge=CHALLENGE)


def test_forged_signature_on_latest_checkpoint_rejected(transcript):
    t = dict(transcript)
    t["latest_checkpoint"] = dict(t["latest_checkpoint"])
    t["latest_checkpoint"]["signature"] = "A" * 86
    # The challenge additionally binds the serialized signed document.
    with pytest.raises(qualifier.OffhostWitnessError):
        qualifier.check_remote_evidence(
            t, challenge=CHALLENGE,
            now_ms=transcript["latest_checkpoint"]["issued_at_ms"] + 1000,
        )


def test_invalid_challenge_shape_denied_before_creating_signing_key():
    with pytest.raises(ValueError, match="challenge"):
        observer.produce_transcript("../../../etc/passwd")


def test_expired_signed_remote_checkpoint_fails_closed(transcript):
    with pytest.raises(qualifier.ObserverCheckpointError):
        qualifier.check_remote_evidence(
            transcript, challenge=CHALLENGE,
            now_ms=transcript["latest_checkpoint"]["expires_at_ms"] + 100,
        )


def test_remote_runner_rejects_unknown_host():
    with pytest.raises(qualifier.OffhostWitnessError, match="pinned"):
        qualifier.run_physical("xwing", SCRIPTS / "temporary_offhost_observer.py")


def test_remote_runner_rejects_absent_source(tmp_path):
    with pytest.raises(qualifier.OffhostWitnessError, match="source"):
        qualifier.run_physical("lenovo", tmp_path / "missing.py")


def test_ssh_outage_yields_no_claim(tmp_path):
    source = tmp_path / "fixture.py"
    source.write_text('print("do not run")\n')
    with patch.object(
        qualifier.subprocess, "run",
        side_effect=subprocess.TimeoutExpired("ssh", 3),
    ):
        with pytest.raises(qualifier.OffhostWitnessError, match="unavailable"):
            qualifier.run_physical("lenovo", source)


def test_ssh_failure_yields_no_claim(tmp_path):
    source = tmp_path / "fixture.py"
    source.write_text('print("do not run")\n')
    response = subprocess.CompletedProcess(["ssh"], 255, b"", b"host offline")
    with patch.object(qualifier.subprocess, "run", return_value=response):
        with pytest.raises(qualifier.OffhostWitnessError, match="failed closed"):
            qualifier.run_physical("lenovo", source)


def test_unparseable_ssh_output_fails_closed(tmp_path):
    source = tmp_path / "fixture.py"
    source.write_text('print("do not run")\n')
    response = subprocess.CompletedProcess(["ssh"], 0, b"not JSON", b"")
    with patch.object(qualifier.subprocess, "run", return_value=response):
        with pytest.raises(qualifier.OffhostWitnessError, match="invalid"):
            qualifier.run_physical("lenovo", source)


def test_oversized_output_fails_closed(tmp_path):
    source = tmp_path / "fixture.py"
    source.write_text('print("do not run")\n')
    response = subprocess.CompletedProcess(
        ["ssh"], 0, b"{" + b"x" * 16000, b"",
    )
    with patch.object(qualifier.subprocess, "run", return_value=response):
        with pytest.raises(qualifier.OffhostWitnessError, match="failed closed"):
            qualifier.run_physical("lenovo", source)


def test_ssh_command_is_strict_and_stdin_only(tmp_path):
    source = tmp_path / "fixture.py"
    source.write_text('print("do not run")\n')
    with patch.object(
        qualifier.subprocess, "run",
        side_effect=subprocess.TimeoutExpired("ssh", 3),
    ) as spy:
        with pytest.raises(qualifier.OffhostWitnessError):
            qualifier.run_physical("lenovo", source)
    args = spy.call_args.args[0]
    assert args[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert "StrictHostKeyChecking=yes" in args
    assert args[-3:-1] == ["python3", "-"]
    assert len(args[-1]) == 32
    assert spy.call_args.kwargs["input"] == source.read_bytes()
    assert spy.call_args.kwargs["timeout"] == 20


def test_temporary_observer_script_does_not_export_private_key(transcript):
    as_text = json.dumps(transcript)
    assert "private_key" not in as_text
    assert "pem" not in as_text.lower()
    assert transcript["production_admission"] == "NO_GO"