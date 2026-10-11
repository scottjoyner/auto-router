"""No-network, synthetic validation for private operator Compose preflight."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "verify_private_compose_candidate_readonly.py"
spec = importlib.util.spec_from_file_location("private_compose", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
IMAGE = "sha256:" + "a" * 64


def _compose(**updates):
    value = {"services": {"llm-router": {"image": IMAGE, "pull_policy": "never"}}}
    value["services"]["llm-router"].update(updates)
    return value


def test_exact_candidate_without_build_or_pull_passes_structural_gate_only():
    report = gate.assess_merged_service(_compose(), service="llm-router", expected_image_id=IMAGE)
    assert report["decision"] == "PASS_PRIVATE_COMPOSE_STRUCTURE_ONLY"
    assert report["production_release_authorized"] is False
    assert report["credential_custody_verified"] is False
    assert report["remote_admin_auth_verified"] is False


@pytest.mark.parametrize("entry,reason", [
    ({"image": "mutable:latest", "pull_policy": "never"}, "COMPOSE_IMAGE_NOT_EXACT_CANDIDATE_ID"),
    ({"image": IMAGE, "pull_policy": "never", "build": "."}, "COMPOSE_BUILD_STILL_PRESENT"),
    ({"image": IMAGE}, "COMPOSE_PULL_POLICY_NOT_NEVER"),
    ({"image": IMAGE, "pull_policy": "always"}, "COMPOSE_PULL_POLICY_NOT_NEVER"),
])
def test_deployment_drift_holds(entry, reason):
    config = {"services": {"llm-router": entry}}
    report = gate.assess_merged_service(config, service="llm-router", expected_image_id=IMAGE)
    assert report["decision"] == "HOLD_PRIVATE_COMPOSE"
    assert reason in report["reason_codes"]


def test_missing_service_or_malformed_structure_holds():
    for obj in (None, {}, {"services": []}, {"services": {"other": {}}}):
        report = gate.assess_merged_service(obj, service="llm-router", expected_image_id=IMAGE)
        assert "ROUTER_SERVICE_MISSING" in report["reason_codes"]


def test_private_compose_merge_does_not_disclose_interpolated_values(monkeypatch, tmp_path):
    filename = tmp_path / "privately-resolved.yml"
    filename.write_text("services: {}\n")
    secret = "SYNTHETIC-NEVER-PRINT-PRIVATE-VALUE"
    payload = _compose()
    payload["services"]["llm-router"]["environment"] = {"SECRET": secret}
    requested = []

    def fake_run(args, **kwargs):
        requested.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode(), stderr=secret.encode())

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    report = gate.run_private_preflight([str(filename)], service="llm-router", expected_image_id=IMAGE)
    assert report["decision"] == "PASS_PRIVATE_COMPOSE_STRUCTURE_ONLY"
    assert secret not in json.dumps(report)
    argv, kwargs = requested[0]
    assert argv[:4] == ["docker", "compose", "-f", str(filename)]
    assert argv[-4:] == ["config", "--no-env-resolution", "--format", "json"]
    assert kwargs["capture_output"] is True
    assert all(x not in argv for x in ("up", "build", "pull", "restart", "down"))


@pytest.mark.parametrize("rc,value", [(1, b"private data"), (0, b"{bad json")])
def test_failures_produce_only_sanitized_hold(monkeypatch, tmp_path, rc, value):
    filename = tmp_path / "compose.yml"
    filename.write_text("services: {}\n")
    monkeypatch.setattr(gate.subprocess, "run",
                        lambda *_args, **_kw: SimpleNamespace(returncode=rc, stdout=value, stderr=b"SECRET"))
    report = gate.run_private_preflight([str(filename)], service="llm-router", expected_image_id=IMAGE)
    assert report["reason_codes"] == ["PRIVATE_COMPOSE_CONFIG_UNAVAILABLE"]
    assert "SECRET" not in json.dumps(report)


def test_missing_file_and_invalid_candidate_hold_before_compose_call(monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError("MUST NOT RUN")
    monkeypatch.setattr(gate.subprocess, "run", forbidden)
    report = gate.run_private_preflight(["/missing/fake"], service="llm-router", expected_image_id=IMAGE)
    assert report["decision"] == "HOLD_PRIVATE_COMPOSE"
    report = gate.run_private_preflight([], service="llm-router", expected_image_id="tag:latest")
    assert report["decision"] == "HOLD_PRIVATE_COMPOSE"
