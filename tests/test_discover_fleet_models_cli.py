"""CLI statistics distinguish serving provider lanes from physical machines."""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

from auto_router.fleet_task_dispatcher import NodeInfo


def test_cli_reports_lanes_and_distinct_hardware(monkeypatch, capsys, tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "discover_fleet_models.py"
    namespace = runpy.run_path(str(script), run_name="fleet_cli_under_test")
    main = namespace["main"]
    nodes = [
        NodeInfo(name="xwing", provider_name="xwing-main", ip="127.0.0.1", online=True,
                 inventory_authoritative=True, loaded_models=["m1"]),
        NodeInfo(name="xwing", provider_name="xwing-specialist", ip="127.0.0.1", online=True,
                 inventory_authoritative=False, all_models=["m2"]),
        NodeInfo(name="destroyer", provider_name="destroyer-k2", ip="127.0.0.1", online=False),
    ]
    monkeypatch.setitem(main.__globals__, "probe_all_nodes", lambda **kw: nodes)
    monkeypatch.setattr(sys, "argv", [str(script), "--json", "--require-loaded-models", "0", "--require-online", "0"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["node_count"] == 3
    assert payload["physical_node_count"] == 2
    assert payload["online_nodes"] == 2
    assert payload["online_physical_nodes"] == 1
    assert payload["authoritative_physical_nodes"] == 1
    assert payload["loaded_models"] == 1
    assert {r["provider_name"] for r in payload["nodes"]} == {"xwing-main", "xwing-specialist", "destroyer-k2"}
    assert payload["status"] == "degraded"
