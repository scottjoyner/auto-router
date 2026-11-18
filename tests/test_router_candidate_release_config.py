"""Socketless release configuration gate: missing/placeholder credentials HOLD."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "candidate_router_release", ROOT / "scripts" / "audit_router_release_configuration.py",
)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

COMPOSE = (ROOT / "docker-compose.yml").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()
BASE = {
    "AUTO_ROUTER_BIND": "127.0.0.1",
    "AUTO_ROUTER_HOST_PORT": "8088",
    "AUTO_ROUTER_ADMIN_TOKEN": "router-admin-K2v9Q5m7R3p8L4x6W1a0Z9n2F7h6",
    "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN": "assistx-inference-P9z2J8w4S6k3B1n5L7x0Y2q9C4",
    "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS": "assistx-callback-T3h7E1v9R5p2D8k4Q0z6N1w9J3",
    "AUTO_ROUTER_ASSISTX_BASIC_AUTH_USER": "assistx-router",
    "AUTO_ROUTER_STRICT_OFFLINE": "true",
    "AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED": "true",
    "AUTO_ROUTER_AUTOLOAD_ENABLED": "false",
    "AUTO_ROUTER_AGENTGATEWAY_ENABLED": "false",
    "AUTO_ROUTER_LOG_PROMPTS": "false",
}


def test_credential_separation_and_code_contract_still_never_grant_cutover():
    proof = mod.check(BASE, COMPOSE, DOCKERFILE)
    assert proof["configuration_checks_complete"]
    assert all(proof["checks"].values())
    assert proof["production_admission"] == "NO_GO"
    assert proof["decision"] == "HOLD"
    assert not proof["live_ipv4_ipv6_and_docker_peer_admin_denial_verified"]
    assert not proof["restorable_backup_and_rollback_verified"]
    redacted = json.dumps(proof)
    assert not any(
        BASE[name] in redacted for name in (
            "AUTO_ROUTER_ADMIN_TOKEN",
            "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN",
            "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS",
        )
    )
    assert not any(key in redacted for key in (
        "operator-admin", "assistx-inference", "assistx-callback",
    ))


@pytest.mark.parametrize(("name", "bad"), [
    ("AUTO_ROUTER_ADMIN_TOKEN", ""),
    ("AUTO_ROUTER_ADMIN_TOKEN", "test"),
    ("AUTO_ROUTER_ADMIN_TOKEN", "a" * 40),
    ("AUTO_ROUTER_ADMIN_TOKEN", "abcd1234" * 12),
    ("AUTO_ROUTER_ADMIN_TOKEN", "change-me" + "Q8w7E6r5T4y3U2i1O9p0" * 2),
    ("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "change-me"),
    ("AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS", "change-me"),
    ("AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS", "a" * 5),
    ("AUTO_ROUTER_ASSISTX_BASIC_AUTH_USER", ""),
    ("AUTO_ROUTER_BIND", "0.0.0.0"),
    ("AUTO_ROUTER_BIND", "::"),
    ("AUTO_ROUTER_BIND", "localhost"),
    ("AUTO_ROUTER_HOST_PORT", "0"),
    ("AUTO_ROUTER_HOST_PORT", "99999"),
    ("AUTO_ROUTER_HOST_PORT", "a" * 10000),
    ("AUTO_ROUTER_STRICT_OFFLINE", "false"),
    ("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "false"),
    ("AUTO_ROUTER_AUTOLOAD_ENABLED", "true"),
    ("AUTO_ROUTER_AGENTGATEWAY_ENABLED", "true"),
    ("AUTO_ROUTER_LOG_PROMPTS", "true"),
])
def test_unsafe_candidate_configuration_fails(name, bad):
    env = {**BASE, name: bad}
    proof = mod.check(env, COMPOSE, DOCKERFILE)
    assert proof["configuration_checks_complete"] is False
    assert proof["decision"] == "HOLD"
    assert bad not in json.dumps(proof) if len(bad) > 8 else True


def test_identical_admin_and_inference_secret_does_not_pass():
    proof = mod.check({
        **BASE,
        "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN": BASE["AUTO_ROUTER_ADMIN_TOKEN"],
    }, COMPOSE, DOCKERFILE)
    assert not proof["checks"]["admin_inference_and_callback_credentials_distinct"]
    assert not proof["configuration_checks_complete"]


def test_identical_inference_and_callback_secret_does_not_pass():
    proof = mod.check({
        **BASE,
        "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS":
            BASE["AUTO_ROUTER_INTERNAL_SERVICE_TOKEN"],
    }, COMPOSE, DOCKERFILE)
    assert not proof["checks"]["admin_inference_and_callback_credentials_distinct"]


def test_source_drift_in_compose_or_image_entrypoint_fails():
    assert not mod.check(
        BASE, COMPOSE.replace(
            "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS: ${AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS:-}",
            "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS: ${AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS:-change-me}",
        ), DOCKERFILE,
    )["configuration_checks_complete"]
    assert not mod.check(
        BASE, COMPOSE,
        DOCKERFILE.replace("auto_router.secure_live:app", "auto_router.main:app"),
    )["configuration_checks_complete"]


def test_no_cli_success_even_on_synthetic_complete_env(tmp_path, monkeypatch, capsys):
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    compose_file = tmp_path / "compose.yaml"
    docker = tmp_path / "Dockerfile"
    compose_file.write_text(COMPOSE)
    docker.write_text(DOCKERFILE)
    with pytest.raises(SystemExit):
        mod.main([])
    assert mod.main([
        "--explicit-read-only-candidate-review",
        "--compose-file", str(compose_file),
        "--dockerfile", str(docker),
    ]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["configuration_checks_complete"]
    assert result["decision"] == "HOLD"
