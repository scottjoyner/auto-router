from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from auto_router import fleet_task_dispatcher as discovery


def _install_transport(monkeypatch, handler) -> None:
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        return real_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr(discovery.httpx, "Client", client_factory)


def _provider(**overrides):
    row = {
        "name": "test-provider",
        "node_id": "node-a",
        "base_url": "http://node-a:1234/v1",
        "configured_models": ["configured-model"],
        "model_aliases": {"local/configured": "configured-model"},
    }
    row.update(overrides)
    return row


def test_api_root_removes_supported_suffixes() -> None:
    assert discovery._api_root("http://node:1234/v1") == "http://node:1234"
    assert discovery._api_root("http://node:1234/api/v1") == "http://node:1234"
    assert discovery._api_root("http://node:1234/api/v0") == "http://node:1234"


def test_provider_registry_expands_defaults_and_preserves_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("TEST_NODE_URL", raising=False)
    path = tmp_path / "providers.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "providers": [
                    {
                        "name": "test",
                        "type": "lmstudio",
                        "node_id": "node-a",
                        "enabled": False,
                        "base_url": "${TEST_NODE_URL:-http://node-a:1234/v1}",
                        "models": [
                            {
                                "alias": "local/model-a",
                                "provider_model": "model-a",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = discovery._provider_rows(path)

    assert rows[0]["base_url"] == "http://node-a:1234/v1"
    assert rows[0]["configured_models"] == ["model-a"]
    assert rows[0]["model_aliases"] == {"local/model-a": "model-a"}


def test_provider_registry_rejects_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text("providers: [", encoding="utf-8")

    with pytest.raises(discovery.DiscoveryConfigError, match="cannot read provider registry"):
        discovery._provider_rows(path)


def test_provider_registry_rejects_duplicate_nodes(tmp_path: Path) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "providers": [
                    {
                        "name": "first",
                        "type": "lmstudio",
                        "node_id": "node-a",
                        "base_url": "http://node-a:1234/v1",
                        "models": [],
                    },
                    {
                        "name": "second",
                        "type": "lmstudio",
                        "node_id": "node-a",
                        "base_url": "http://node-b:1234/v1",
                        "models": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(discovery.DiscoveryConfigError, match="duplicate LM Studio node_id"):
        discovery._provider_rows(path)


def test_provider_registry_rejects_url_credentials(tmp_path: Path) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "providers": [
                    {
                        "name": "test",
                        "type": "lmstudio",
                        "node_id": "node-a",
                        "base_url": "http://user:secret@node-a:1234/v1",
                        "models": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(discovery.DiscoveryConfigError, match="api_token_env"):
        discovery._provider_rows(path)


def test_probe_separates_observed_and_configured_inventory(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={"models": [{"key": "large-model"}, {"key": "small-model"}]},
            )
        if request.url.path == "/api/v0/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "large-model", "state": "loaded"},
                        {"id": "small-model", "state": "not-loaded"},
                    ]
                },
            )
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "large-model"}]})
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)
    node = discovery._probe_provider(_provider())

    assert node.online is True
    assert node.inventory_complete is True
    assert node.inventory_authoritative is True
    assert node.loaded_state_source == "native"
    assert node.loaded_models == ["large-model"]
    assert node.all_models == ["large-model", "small-model"]
    assert node.configured_models == ["configured-model"]
    assert "configured-model" not in node.all_models
    assert node.model_aliases == {"local/configured": "configured-model"}
    assert any("configured models not observed" in warning for warning in node.warnings)


def test_openai_only_inventory_is_visible_but_not_routable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)
    node = discovery._probe_provider(_provider(configured_models=["model-a"]))

    assert node.online is True
    assert node.inventory_complete is False
    assert node.inventory_authoritative is False
    assert node.loaded_state_source == "compatibility-inferred"
    assert node.loaded_models == []
    assert node.all_models == ["model-a"]
    assert any("cannot assert" in warning for warning in node.warnings)
    assert node.endpoint_status["api/v1/models"] == "http-404"


def test_partial_endpoint_failures_remain_visible_on_online_node(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(401, json={"error": "unauthorized"})
        if request.url.path == "/api/v0/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "model-a", "state": "loaded"}]},
            )
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)
    node = discovery._probe_provider(_provider(configured_models=["model-a"]))

    assert node.online is True
    assert node.error == ""
    assert node.endpoint_status["api/v1/models"] == "http-401"
    assert any("api/v1/models: http-401" in warning for warning in node.warnings)


def test_probe_uses_token_environment_without_leaking_it(monkeypatch) -> None:
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("authorization", ""))
        return httpx.Response(503, json={"error": "offline"})

    monkeypatch.setenv("TEST_LM_TOKEN", "super-secret")
    _install_transport(monkeypatch, handler)
    node = discovery._probe_provider(_provider(api_token_env="TEST_LM_TOKEN"))

    assert seen_headers == ["Bearer super-secret"] * 3
    assert node.online is False
    assert "super-secret" not in node.error
    assert node.all_models == []
    assert node.configured_models == ["configured-model"]


def test_present_registry_with_no_lmstudio_entries_never_uses_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_path = tmp_path / "providers.yaml"
    provider_path.write_text(
        "providers:\n  - name: other\n    type: openai\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps({"nodes": [{"name": "stale-node", "online": True}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "DEFAULT_REPORT_PATH", report_path)

    assert discovery.probe_all_nodes(provider_config=provider_path) == []


def test_live_registry_never_recycles_cached_report(tmp_path: Path, monkeypatch) -> None:
    provider_path = tmp_path / "providers.yaml"
    provider_path.write_text(
        "providers:\n"
        "  - name: test\n"
        "    type: lmstudio\n"
        "    node_id: node-a\n"
        "    enabled: true\n"
        "    base_url: http://node-a:1234/v1\n"
        "    models: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        discovery,
        "_probe_provider",
        lambda provider, timeout_seconds: discovery.NodeInfo(
            name=provider["node_id"],
            ip="node-a",
            online=False,
            error="connection refused",
        ),
    )

    nodes = discovery.probe_all_nodes(provider_config=provider_path)

    assert len(nodes) == 1
    assert nodes[0].name == "node-a"
    assert nodes[0].discovery_source == "live"
    assert nodes[0].inventory_authoritative is False


def test_missing_registry_cache_is_visible_but_not_routable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing_registry = tmp_path / "missing.yaml"
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "name": "cached-node",
                        "online": True,
                        "loaded_models": ["old-model"],
                        "all_models": ["old-model"],
                        "inventory_complete": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "DEFAULT_REPORT_PATH", report_path)
    monkeypatch.setattr(discovery, "DEFAULT_STATS_PATH", tmp_path / "missing-stats.json")

    nodes = discovery.probe_all_nodes(provider_config=missing_registry)

    assert nodes[0].discovery_source == "cached-report"
    assert nodes[0].inventory_complete is True
    assert nodes[0].inventory_authoritative is False
    assert nodes[0].loaded_models == []
    assert nodes[0].all_models == ["old-model"]
    assert any("diagnostic-only" in warning for warning in nodes[0].warnings)


def test_cached_stats_are_visible_but_not_routable(tmp_path: Path, monkeypatch) -> None:
    missing_registry = tmp_path / "missing.yaml"
    monkeypatch.setattr(discovery, "DEFAULT_REPORT_PATH", tmp_path / "missing-report.json")
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(
        json.dumps({"slots": [{"node": "cached-node", "model": "old-model"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "DEFAULT_STATS_PATH", stats_path)

    nodes = discovery.probe_all_nodes(provider_config=missing_registry)

    assert nodes[0].discovery_source == "cached-stats"
    assert nodes[0].inventory_authoritative is False
    assert nodes[0].loaded_models == []
    assert nodes[0].all_models == ["old-model"]


def test_unexpected_node_failure_does_not_abort_other_probes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_path = tmp_path / "providers.yaml"
    provider_path.write_text(
        yaml.safe_dump(
            {
                "providers": [
                    {
                        "name": "first",
                        "type": "lmstudio",
                        "node_id": "node-a",
                        "base_url": "http://node-a:1234/v1",
                        "models": [],
                    },
                    {
                        "name": "second",
                        "type": "lmstudio",
                        "node_id": "node-b",
                        "base_url": "http://node-b:1234/v1",
                        "models": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    def probe(provider, timeout_seconds):
        if provider["node_id"] == "node-a":
            raise RuntimeError("boom")
        return discovery.NodeInfo(name="node-b", ip="node-b", online=True)

    monkeypatch.setattr(discovery, "_probe_provider", probe)
    nodes = discovery.probe_all_nodes(provider_config=provider_path)

    assert [node.name for node in nodes] == ["node-a", "node-b"]
    assert nodes[0].online is False
    assert "unexpected probe failure" in nodes[0].error
    assert nodes[1].online is True


def test_invalid_probe_limits_fail_before_network_access(tmp_path: Path) -> None:
    provider_path = tmp_path / "providers.yaml"
    provider_path.write_text("providers: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="timeout_seconds"):
        discovery.probe_all_nodes(provider_config=provider_path, timeout_seconds=0)
    with pytest.raises(ValueError, match="max_workers"):
        discovery.probe_all_nodes(provider_config=provider_path, max_workers=0)
