from __future__ import annotations

from pathlib import Path

import httpx
import yaml

from auto_router import fleet_task_dispatcher as discovery


def test_api_root_removes_openai_suffix() -> None:
    assert discovery._api_root("http://node:1234/v1") == "http://node:1234"
    assert discovery._api_root("http://node:1234/api/v1") == "http://node:1234"


def test_provider_registry_expands_defaults(tmp_path: Path, monkeypatch) -> None:
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
                        "models": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rows = discovery._provider_rows(path)
    assert rows[0]["base_url"] == "http://node-a:1234/v1"
    assert rows[0]["node_id"] == "node-a"


def test_probe_combines_inventory_and_loaded_state(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": [{"key": "large-model"}, {"key": "small-model"}]})
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

    real_client = httpx.Client
    monkeypatch.setattr(
        discovery.httpx,
        "Client",
        lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")),
    )
    node = discovery._probe_provider(
        {
            "name": "test-provider",
            "node_id": "node-a",
            "base_url": "http://node-a:1234/v1",
            "models": [],
        }
    )
    assert node.online is True
    assert node.inventory_complete is True
    assert node.loaded_models == ["large-model"]
    assert node.all_models == ["large-model", "small-model"]


def test_live_registry_never_recycles_cached_report(tmp_path: Path, monkeypatch) -> None:
    provider_path = tmp_path / "providers.yaml"
    provider_path.write_text(
        "providers:\n  - name: test\n    type: lmstudio\n    node_id: node-a\n    enabled: true\n    base_url: http://node-a:1234/v1\n    models: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        discovery,
        "_probe_provider",
        lambda provider, timeout_seconds: discovery.NodeInfo(
            name=provider["node_id"], ip="node-a", online=False, error="connection refused"
        ),
    )
    nodes = discovery.probe_all_nodes(provider_config=provider_path)
    assert len(nodes) == 1
    assert nodes[0].name == "node-a"
    assert nodes[0].discovery_source == "live"
