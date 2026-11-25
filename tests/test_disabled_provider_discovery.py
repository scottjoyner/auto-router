from __future__ import annotations

from pathlib import Path

import yaml

from auto_router import fleet_task_dispatcher as discovery


def write_config(path: Path, providers: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"providers": providers}), encoding="utf-8")


def test_disabled_provider_is_never_probed(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "providers.yaml"
    write_config(
        config,
        [
            {
                "name": "drained-node",
                "type": "lmstudio",
                "node_id": "node-a",
                "enabled": False,
                # Intentionally invalid/public: disabled rows are not active
                # discovery configuration and must not block or trigger access.
                "base_url": "https://example.com/v1",
                "models": [],
            }
        ],
    )
    calls: list[dict] = []

    def probe(provider, *, timeout_seconds):
        calls.append(provider)
        raise AssertionError("disabled provider was probed")

    monkeypatch.setattr(discovery, "_probe_provider", probe)

    assert discovery.probe_all_nodes(provider_config=config) == []
    assert calls == []


def test_string_false_disables_provider(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "providers.yaml"
    write_config(
        config,
        [
            {
                "name": "drained-node",
                "type": "lmstudio",
                "node_id": "node-a",
                "enabled": "false",
                "base_url": "http://node-a:1234/v1",
                "models": [],
            }
        ],
    )
    monkeypatch.setattr(
        discovery,
        "_probe_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled provider was probed")
        ),
    )

    assert discovery.probe_all_nodes(provider_config=config) == []


def test_disabled_duplicate_does_not_conflict_with_active_node(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "providers.yaml"
    write_config(
        config,
        [
            {
                "name": "old-path",
                "type": "lmstudio",
                "node_id": "node-a",
                "enabled": False,
                "base_url": "http://old-node-a:1234/v1",
                "models": [],
            },
            {
                "name": "active-path",
                "type": "lmstudio",
                "node_id": "node-a",
                "enabled": True,
                "base_url": "http://node-a:1234/v1",
                "models": [],
            },
        ],
    )

    monkeypatch.setattr(
        discovery,
        "_probe_provider",
        lambda provider, *, timeout_seconds: discovery.NodeInfo(
            name=provider["node_id"],
            ip="node-a",
            online=True,
        ),
    )

    nodes = discovery.probe_all_nodes(provider_config=config)

    assert [node.name for node in nodes] == ["node-a"]


def test_registry_with_only_disabled_nodes_does_not_resurrect_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "providers.yaml"
    write_config(
        config,
        [
            {
                "name": "disabled",
                "type": "lmstudio",
                "node_id": "node-a",
                "enabled": False,
                "base_url": "http://node-a:1234/v1",
                "models": [],
            }
        ],
    )
    report = tmp_path / "report.json"
    report.write_text(
        '{"nodes":[{"name":"node-a","online":true,"loaded_models":["stale"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "DEFAULT_REPORT_PATH", report)

    assert discovery.probe_all_nodes(provider_config=config) == []


def test_two_active_lanes_on_same_host_are_independently_probed(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "providers.yaml"
    write_config(config, [
        {"name": "xwing-main", "type": "lmstudio", "node_id": "xwing", "enabled": True,
         "base_url": "http://node-a:1234/v1", "models": []},
        {"name": "xwing-specialist", "type": "lmstudio", "node_id": "xwing", "enabled": True,
         "base_url": "http://node-a:1238/v1", "models": []},
        {"name": "xwing-disabled", "type": "lmstudio", "node_id": "xwing", "enabled": False,
         "base_url": "https://example.com/v1", "models": []},
    ])
    visited = []
    def probe(provider, *, timeout_seconds):
        visited.append(provider["name"])
        return discovery.NodeInfo(
            name=provider["node_id"], ip="node-a", online=True,
            provider_name=provider["name"],
            discovery_source="live", inventory_authoritative=False,
        )
    monkeypatch.setattr(discovery, "_probe_provider", probe)
    nodes = discovery.probe_all_nodes(provider_config=config)
    assert {node.name for node in nodes} == {"xwing"}
    assert {node.provider_name for node in nodes} == {"xwing-main", "xwing-specialist"}
    assert set(visited) == {"xwing-main", "xwing-specialist"}
    assert len(nodes) == 2
    assert all(not node.inventory_authoritative for node in nodes)
