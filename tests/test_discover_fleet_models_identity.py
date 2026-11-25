"""A native inventory is necessary but not sufficient for route identity."""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

from auto_router.fleet_task_dispatcher import NodeInfo


def _rows():
    return [
        NodeInfo(name="xwing", provider_name="xwing-native", ip="127.0.0.1", online=True,
                 inventory_authoritative=True, configured_models=["ornith-old", "embed"],
                 loaded_models=["ornith-new", "embed"]),
        NodeInfo(name="beelink", provider_name="beelink-native", ip="127.0.0.1", online=True,
                 inventory_authoritative=True, configured_models=["liquid"],
                 loaded_models=["liquid"]),
        NodeInfo(name="destroyer", provider_name="destroyer-compat", ip="127.0.0.1", online=True,
                 inventory_authoritative=False, configured_models=["k2"], all_models=["k2"],
                 loaded_models=["k2"]),
        NodeInfo(name="offline", provider_name="offline", ip="127.0.0.1", online=False,
                 configured_models=["z"], loaded_models=["z"]),
    ]


def test_native_exact_counts_never_promote_aliases_or_compatibility():
    namespace=runpy.run_path(str(Path(__file__).resolve().parents[1]/"scripts"/"discover_fleet_models.py"),run_name="cli_unit")
    result=namespace["strict_native_loaded_model_counts"](_rows())
    assert result=={"native_loaded_exact_configured_ids":2,
                    "native_loaded_unmatched_ids":1,
                    "online_non_authoritative_lanes":1}


def test_cli_exposes_non_admission_and_rejects_high_exact_threshold(monkeypatch,capsys):
    script=Path(__file__).resolve().parents[1]/"scripts"/"discover_fleet_models.py"
    ns=runpy.run_path(str(script),run_name="cli_unit")
    monkeypatch.setitem(ns["main"].__globals__,"probe_all_nodes",lambda **kwargs:_rows())
    monkeypatch.setattr(sys,"argv",[str(script),"--json","--require-online","0",
       "--require-loaded-models","0","--require-exact-configured-loaded","3"])
    assert ns["main"]()==6
    read=capsys.readouterr()
    parsed=json.loads(read.out)
    assert parsed["loaded_models"]==3 # only online native-authoritative inventory
    assert parsed["native_loaded_exact_configured_ids"]==2
    assert parsed["native_loaded_unmatched_ids"]==1
    assert "NOT signed runtime admission" in parsed["note"]
    assert "FAIL exact native" in read.err
