"""Issue #37 multi-service residency preflight contract (no network in tests)."""
from __future__ import annotations

import json

import pytest
import yaml

from auto_router.fleet_task_dispatcher import DiscoveryConfigError, NodeInfo
from scripts import p0_37_provider_residency_preflight as preflight


def _config(tmp_path, rows):
    path = tmp_path / "providers.yaml"
    path.write_text(yaml.safe_dump({"providers": rows}), encoding="utf-8")
    return path


def _service(name, node, port, *, models=None):
    return {
        "type": "lmstudio",
        "name": name,
        "node_id": node,
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": models or [],
    }


def test_same_physical_node_can_run_multiple_distinct_provider_services(tmp_path):
    path = _config(tmp_path, [
        _service("xwing-lmstudio", "xwing", 1234),
        _service("xwing-llama", "xwing", 18270),
        _service("destroyer-ornith", "destroyer", 1234),
    ])
    services = preflight.configured_services(path)
    result = preflight.summarize(services)
    assert result["provider_services"] == 3
    assert result["physical_nodes"] == 2
    assert result["multi_service_nodes"] == {
        "xwing": ["xwing-llama", "xwing-lmstudio"]
    }
    assert result["network_probed"] is False
    assert result["canary_candidate_services"] == 0
    assert all(row["resident_model_count"] is None for row in result["services"])


def test_config_only_mode_makes_no_network_probes(tmp_path, monkeypatch):
    services = preflight.configured_services(
        _config(tmp_path, [_service("xwing-lmstudio", "xwing", 1234)])
    )

    def disallowed(*args, **kwargs):
        raise AssertionError("config-only mode must not probe the network")

    monkeypatch.setattr(preflight, "_probe_provider", disallowed)
    result = preflight.summarize(services, probe=False)
    assert result["services"][0]["inventory_state"] == "not_probed"


def test_authoritative_loaded_state_is_required_for_candidate(monkeypatch, tmp_path):
    services = preflight.configured_services(_config(tmp_path, [
        _service("real-native", "xwing", 1234),
        _service("compat-only", "xwing", 18270),
        _service("unloaded", "destroyer", 1234),
    ]))

    def fake_probe(service, *, timeout_seconds):
        assert timeout_seconds == 0.8
        provider = service["name"]
        is_native = provider != "compat-only"
        is_loaded = provider != "unloaded"
        return NodeInfo(
            name=service["node_id"],
            ip="127.0.0.1",
            online=True,
            provider_name=provider,
            all_models=["fixture/model"],
            loaded_models=["fixture/model"] if is_loaded else [],
            inventory_authoritative=is_native,
        )

    monkeypatch.setattr(preflight, "_probe_provider", fake_probe)
    result = preflight.summarize(services, probe=True, timeout_seconds=0.8)
    by_name = {row["provider_service"]: row for row in result["services"]}
    assert by_name["real-native"]["canary_candidate"] is True
    assert by_name["real-native"]["resident_model_count"] == 1
    assert by_name["compat-only"]["canary_candidate"] is False
    assert by_name["compat-only"]["resident_model_count"] is None
    assert by_name["compat-only"]["resident_models"] == []
    assert by_name["unloaded"]["canary_candidate"] is False
    assert by_name["unloaded"]["resident_model_count"] == 0
    assert result["provider_observer_attestation"] is False
    assert result["two_user_execution_proven"] is False


def test_probe_errors_do_not_promote_configured_models_to_loaded(monkeypatch, tmp_path):
    services = preflight.configured_services(_config(tmp_path, [
        _service(
            "unreachable",
            "destroyer",
            1234,
            models=[{"alias": "fake", "provider_model": "pretend-installed"}],
        )
    ]))

    def unavailable(*args, **kwargs):
        raise TimeoutError("provider not reachable")

    monkeypatch.setattr(preflight, "_probe_provider", unavailable)
    result = preflight.summarize(services, probe=True)
    row = result["services"][0]
    assert row["inventory_state"] == "probe_error"
    assert row["resident_model_count"] is None
    assert row["resident_models"] == []
    assert row["canary_candidate"] is False


def test_invalid_public_host_and_duplicate_service_names_are_rejected(tmp_path):
    bad = _service("public", "remote", 1234)
    bad["base_url"] = "https://example.com/v1"
    with pytest.raises(DiscoveryConfigError):
        preflight.configured_services(_config(tmp_path, [bad]))
    with pytest.raises(DiscoveryConfigError, match="duplicate provider service name"):
        preflight.configured_services(_config(tmp_path, [
            _service("same", "xwing", 1234),
            _service("same", "destroyer", 1234),
        ]))


def test_cli_config_only_json_reports_unknown_not_unloaded(tmp_path, capsys):
    path = _config(tmp_path, [
        _service("xwing-lmstudio", "xwing", 1234),
        _service("xwing-llama", "xwing", 18270),
    ])
    exit_code = preflight.main([
        "--config", str(path), "--json", "--max-services", "2"
    ])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["network_probed"] is False
    assert output["multi_service_nodes"]["xwing"] == [
        "xwing-llama", "xwing-lmstudio"
    ]
    assert all(
        row["inventory_state"] == "not_probed"
        and row["resident_model_count"] is None
        for row in output["services"]
    )


@pytest.mark.parametrize("workers,timeout", [(0, 1), (5, 1), (1, 0), (1, 100)])
def test_probe_budgets_are_bounded(workers, timeout):
    with pytest.raises(ValueError):
        preflight.summarize([], max_workers=workers, timeout_seconds=timeout)


def test_authoritative_residency_is_never_sufficient_for_physical_admission(
    monkeypatch, tmp_path,
):
    services = preflight.configured_services(
        _config(tmp_path, [_service("resident-service", "beelink", 1234)])
    )

    def resident_response(service, *, timeout_seconds):
        return NodeInfo(
            name=service["node_id"],
            ip="127.0.0.1",
            online=True,
            loaded_models=["resident/model"],
            all_models=["resident/model"],
            inventory_authoritative=True,
        )

    monkeypatch.setattr(preflight, "_probe_provider", resident_response)
    output = preflight.summarize(services, probe=True)
    assert output["inventory_candidate_services"] == 1
    assert output["physical_inference_permitted"] is False
    assert output["host_pressure_checked"] is False
    assert output["operator_approved"] is False
    row = output["services"][0]
    assert row["inventory_candidate"] is True
    assert row["candidate_evidence_scope"] == "resident_inventory_only"
    assert row["host_pressure_checked"] is False
    assert row["physical_inference_permitted"] is False
