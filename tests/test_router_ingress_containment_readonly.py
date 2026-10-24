"""Read-only, no-network synthetic contracts for router ingress dependency HOLD."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "verify_router_ingress_containment_readonly.py"
spec = importlib.util.spec_from_file_location("ingress_containment", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

SECRET = "SYNTHETIC_SHOULD_NOT_APPEAR_IN_REPORT"
ORIGIN = "http://synthetic-router.internal:8088"


def _service(name: str, networks: list[str], *, token: str = SECRET, origin: str = ORIGIN):
    return {
        "Name": "/" + name,
        "State": {"Running": True},
        "Config": {"Env": [
            "AUTO_ROUTER_ADMIN_TOKEN=" + token,
            "AUTO_ROUTER_BASE_URL=" + origin,
            "OTHER=UNRELATED_PRIVATE_ENV",
        ]},
        "NetworkSettings": {
            "Networks": {k: {} for k in networks},
            "Ports": {"8088/tcp": [{"HostIp": "0.0.0.0"}, {"HostIp": "::"}]}
            if name == "git-router-1" else {},
        },
    }


def _fleet():
    return [
        _service("git-router-1", ["router-only", "shared-api"]),
        _service("assistx-api", ["shared-api", "consumer-only"]),
        _service("assistx-worker", ["consumer-only"]),
        _service("assistx-hermes-adapter", ["consumer-only"]),
    ]


def test_realistic_cross_network_topology_is_sanitized_hold():
    result = gate.summarize_containers(_fleet(), {
        "ipv4_forward": False, "ipv6_forward": False, "nft_ruleset": False
    })
    assert result["decision"] == "HOLD_OWNER_CONTROLS_REQUIRED"
    assert result["router_published_wildcard_ipv4"] is True
    assert result["router_published_wildcard_ipv6"] is True
    assert result["consumers"]["assistx-api"]["shared_docker_network"] is True
    assert result["consumers"]["assistx-worker"]["shared_docker_network"] is False
    assert result["consumers"]["assistx-hermes-adapter"]["shared_docker_network"] is False
    assert "PRIVILEGED_NETFILTER_PATH_UNVERIFIED" in result["reason_codes"]
    assert "FORWARDED_AND_HAIRPIN_SOURCE_IDENTITY_UNVERIFIED" in result["reason_codes"]
    assert "ADMIN_TOKEN_CONFIGURED_OVER_PLAINTEXT_HTTP" in result["reason_codes"]
    assert result["containment_change_authorized"] is False
    assert result["production_release_authorized"] is False
    encoded = json.dumps(result)
    for value in (SECRET, ORIGIN, "synthetic-router.internal", "UNRELATED_PRIVATE_ENV",
                  "shared-api", "router-only", "consumer-only"):
        assert value not in encoded


def test_even_all_networks_and_firewall_readability_do_not_authorize_rules():
    fleet = [_service(name, ["shared"]) for name in gate.NAMES]
    for row in fleet:
        row["NetworkSettings"]["Ports"] = {}
    result = gate.summarize_containers(fleet, {
        "ipv4_forward": True, "ipv6_forward": True, "nft_ruleset": True
    })
    assert result["privileged_netfilter_path_readable"] is True
    assert result["decision"] == "HOLD_OWNER_CONTROLS_REQUIRED"
    assert "FORWARDED_AND_HAIRPIN_SOURCE_IDENTITY_UNVERIFIED" in result["reason_codes"]
    assert "OWNER_CONTAINMENT_AND_ROLLBACK_APPROVAL_REQUIRED" in result["reason_codes"]
    assert result["firewall_rule_safety_verified"] is False


def test_missing_container_metadata_is_fail_closed():
    report = gate.summarize_containers(None, None)
    assert "EXPECTED_SERVICE_METADATA_MISSING" in report["reason_codes"]
    assert report["decision"] == "HOLD_OWNER_CONTROLS_REQUIRED"


@pytest.mark.parametrize("url", [
    "http://somehost:8088/?token=secret",
    "http://somehost:8088/admin/context",
    "http://u:pass@somehost:8088",
    "https://somehost:8089",
])
def test_invalid_or_sensitive_origin_never_approved(url):
    report = gate.summarize_containers([
        _service("git-router-1", ["router"]),
        _service("assistx-api", ["router"], origin=url),
        _service("assistx-worker", ["router"]),
        _service("assistx-hermes-adapter", ["router"]),
    ], {})
    assert "CLIENT_ADMIN_ORIGIN_INVALID" in report["reason_codes"]
    assert "somehost" not in json.dumps(report)
    assert "secret" not in json.dumps(report)
    assert "pass" not in json.dumps(report)


def test_no_credentials_and_stopped_consumer_fail_closed():
    fleet = _fleet()
    fleet[2]["Config"]["Env"] = ["AUTO_ROUTER_ADMIN_TOKEN="]
    fleet[3]["State"]["Running"] = False
    report = gate.summarize_containers(fleet, {})
    assert "CLIENT_UNAVAILABLE_OR_MISSING_ADMIN_CREDENTIAL" in report["reason_codes"]


def test_main_uses_only_read_commands_and_never_prints_private_metadata(monkeypatch, capsys):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        if argv[0] == "docker":
            return SimpleNamespace(returncode=0, stdout=json.dumps(_fleet()).encode(),
                                   stderr=SECRET.encode())
        return SimpleNamespace(returncode=1, stdout=b"", stderr=SECRET.encode())

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.main() == 2
    captured = capsys.readouterr().out
    obj = json.loads(captured)
    assert obj["decision"] == "HOLD_OWNER_CONTROLS_REQUIRED"
    assert SECRET not in captured
    assert all(argv[:2] in (["docker", "inspect"], ["iptables", "-S"],
                             ["ip6tables", "-S"], ["nft", "-j"]) for argv in seen)
    assert all(x not in argv for argv in seen for x in ("-A", "-I", "-D", "up", "restart", "build"))


def test_malformed_docker_output_does_not_leak_or_crash(monkeypatch, capsys):
    monkeypatch.setattr(gate.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(
        returncode=0, stdout=b"INVALID_PRIVATE_DOCKER_BLOB", stderr=b""))
    assert gate.main() == 2
    report = capsys.readouterr().out
    assert "INVALID_PRIVATE_DOCKER_BLOB" not in report
    assert json.loads(report)["decision"] == "HOLD_OWNER_CONTROLS_REQUIRED"
