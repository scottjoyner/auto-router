from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from auto_router.fleet_task_dispatcher import NodeInfo


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_fleet_loadouts.py"
SPEC = importlib.util.spec_from_file_location(
    "fleet_loadout_builder_under_test",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def _node(**overrides) -> NodeInfo:
    values = {
        "name": "node-a",
        "ip": "127.0.0.1",
        "online": True,
        "loaded_models": ["model-a"],
        "all_models": ["model-a"],
        "configured_models": ["model-a"],
        "model_aliases": {"local/model-a": "model-a"},
        "latency_ms": 12.0,
        "error": "",
        "warnings": [],
        "endpoint_status": {"api/v1/models": "ok:1"},
        "power_watts": 0.0,
        "base_url": "http://127.0.0.1:1234",
        "provider_name": "node-a",
        "discovery_source": "live",
        "inventory_complete": True,
        "inventory_authoritative": True,
        "loaded_state_source": "native",
        "observed_at": "2026-08-04T17:00:00+00:00",
    }
    values.update(overrides)
    return NodeInfo(**values)


def test_candidates_require_authoritative_online_inventory() -> None:
    assert builder.build_candidates(
        [_node()],
        {},
        "coding_high_throughput",
    )
    assert builder.build_candidates(
        [_node(inventory_authoritative=False)],
        {},
        "coding_high_throughput",
    ) == []
    assert builder.build_candidates(
        [_node(online=False)],
        {},
        "coding_high_throughput",
    ) == []


def test_snapshot_preserves_provenance_and_routable_view() -> None:
    authoritative = _node()
    observed_only = _node(
        name="node-b",
        provider_name="node-b",
        inventory_complete=False,
        inventory_authoritative=False,
        loaded_state_source="compatibility-inferred",
        warnings=["loaded state unavailable"],
    )

    payload = builder._snapshot_payload(
        [observed_only, authoritative],
        {},
        [],
        [],
    )
    rows = {row["name"]: row for row in payload["nodes"]}

    assert rows["node-a"]["routable_loaded_models"] == ["model-a"]
    assert rows["node-a"]["inventory_authoritative"] is True
    assert rows["node-a"]["loaded_state_source"] == "native"
    assert rows["node-a"]["configured_models"] == ["model-a"]
    assert rows["node-a"]["model_aliases"] == {
        "local/model-a": "model-a"
    }
    assert rows["node-b"]["loaded_models"] == ["model-a"]
    assert rows["node-b"]["routable_loaded_models"] == []
    assert rows["node-b"]["warnings"] == ["loaded state unavailable"]


def test_persistence_rows_exclude_non_authoritative_inventory() -> None:
    payload = builder._snapshot_payload(
        [
            _node(),
            _node(
                name="node-b",
                provider_name="node-b",
                inventory_authoritative=False,
            ),
            _node(name="node-c", provider_name="node-c", online=False),
        ],
        {},
        [],
        [],
    )

    rows = builder._authoritative_model_rows(payload["nodes"])

    assert len(rows) == 1
    assert rows[0]["node_name"] == "node-a"
    assert rows[0]["model_id"] == "model-a"
    assert rows[0]["inventory_authoritative"] is True
