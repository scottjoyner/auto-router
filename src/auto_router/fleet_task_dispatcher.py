from __future__ import annotations

"""Fallback fleet node probing helpers used by fleet loadout rebuilding.

The original fleet task dispatcher module is not present in this checkout, but the
loadout rebuilder only needs a lightweight node view: name, IP, online status,
loaded models, the full model inventory, latency, error, and power draw.

This module reconstructs that view from the last persisted fleet loadout report,
and falls back to the dispatcher stats snapshot when needed.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = Path(
    __import__("os").getenv("AUTO_ROUTER_FLEET_LOADOUT_REPORT_PATH", str(REPO_ROOT / "data" / "fleet_loadout_report.json"))
)
DEFAULT_STATS_PATH = Path(
    __import__("os").getenv("AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH", str(REPO_ROOT / "data" / "fleet_dispatcher_stats.json"))
)


@dataclass
class NodeInfo:
    name: str
    ip: str
    online: bool
    loaded_models: list[str] = field(default_factory=list)
    all_models: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str = ""
    power_watts: float = 0.0


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _from_report(report: dict[str, Any]) -> list[NodeInfo]:
    nodes: list[NodeInfo] = []
    for row in report.get("nodes") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        nodes.append(
            NodeInfo(
                name=name,
                ip=str(row.get("ip") or ""),
                online=bool(row.get("online")),
                loaded_models=[str(model) for model in row.get("loaded_models") or [] if str(model).strip()],
                all_models=[str(model) for model in row.get("all_models") or [] if str(model).strip()],
                latency_ms=float(row.get("latency_ms") or 0.0),
                error=str(row.get("error") or ""),
                power_watts=float(row.get("power_watts") or 0.0),
            )
        )
    return nodes


def _from_stats(stats: dict[str, Any]) -> list[NodeInfo]:
    by_node: dict[str, dict[str, Any]] = {}
    for row in stats.get("slots") or []:
        if not isinstance(row, dict):
            continue
        node_name = str(row.get("node") or "").strip()
        model_id = str(row.get("model") or "").strip()
        if not node_name:
            continue
        entry = by_node.setdefault(
            node_name,
            {
                "name": node_name,
                "ip": "",
                "online": bool(row.get("in_flight") is not None),
                "loaded_models": [],
                "all_models": [],
                "latency_ms": 0.0,
                "error": "",
                "power_watts": 0.0,
            },
        )
        if model_id:
            entry["loaded_models"].append(model_id)
            entry["all_models"].append(model_id)
    return [
        NodeInfo(
            name=row["name"],
            ip=row["ip"],
            online=row["online"],
            loaded_models=row["loaded_models"],
            all_models=row["all_models"],
            latency_ms=row["latency_ms"],
            error=row["error"],
            power_watts=row["power_watts"],
        )
        for row in by_node.values()
    ]


def probe_all_nodes() -> list[NodeInfo]:
    """Return a best-effort fleet node snapshot.

    Priority order:
    1. the last persisted fleet loadout report, which contains per-node model and
       latency information;
    2. the fleet dispatcher stats snapshot, which at least captures node/model
       pairings in its slot table.
    """

    report = _load_json(DEFAULT_REPORT_PATH)
    nodes = _from_report(report)
    if nodes:
        return sorted(nodes, key=lambda node: node.name.lower())

    stats = _load_json(DEFAULT_STATS_PATH)
    nodes = _from_stats(stats)
    return sorted(nodes, key=lambda node: node.name.lower())
