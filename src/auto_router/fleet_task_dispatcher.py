from __future__ import annotations

"""Live LM Studio fleet discovery used by loadout rebuilding.

The previous fallback implementation recycled the last generated report and
therefore could never recover from an empty or incorrect snapshot. This module
now probes every configured local LM Studio runtime directly and uses persisted
files only when no provider registry is available.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROVIDER_CONFIG = Path(
    os.getenv("AUTO_ROUTER_PROVIDER_CONFIG", str(REPO_ROOT / "config" / "providers.yaml"))
)
DEFAULT_REPORT_PATH = Path(
    os.getenv(
        "AUTO_ROUTER_FLEET_LOADOUT_REPORT_PATH",
        str(REPO_ROOT / "data" / "fleet_loadout_report.json"),
    )
)
DEFAULT_STATS_PATH = Path(
    os.getenv(
        "AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH",
        str(REPO_ROOT / "data" / "fleet_dispatcher_stats.json"),
    )
)
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("AUTO_ROUTER_DISCOVERY_TIMEOUT_SECONDS", "3"))
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


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
    base_url: str = ""
    provider_name: str = ""
    discovery_source: str = "live"
    inventory_complete: bool = False


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _expand_env(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2) or ""
        return os.getenv(name, default)

    return _ENV_PATTERN.sub(replace, value)


def _api_root(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    for suffix in ("/v1", "/api/v1", "/api/v0"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _host(base_url: str) -> str:
    return urlsplit(base_url).hostname or ""


def _provider_rows(path: Path = DEFAULT_PROVIDER_CONFIG) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = document.get("providers") or []
    providers: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("type") or "").lower() != "lmstudio":
            continue
        base_url = _expand_env(str(row.get("base_url") or "")).strip()
        node_id = str(row.get("node_id") or row.get("name") or "").strip()
        if not base_url or not node_id:
            continue
        providers.append({**row, "base_url": base_url, "node_id": node_id})
    return providers


def _model_id(row: dict[str, Any]) -> str:
    return str(
        row.get("id")
        or row.get("key")
        or row.get("model_key")
        or row.get("model")
        or row.get("identifier")
        or ""
    ).strip()


def _inventory_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("models")
    if not isinstance(rows, list):
        rows = payload.get("data")
    return [row for row in (rows or []) if isinstance(row, dict)]


def _loaded_from_row(row: dict[str, Any]) -> bool:
    state = str(row.get("state") or row.get("status") or "").lower()
    if state in {"loaded", "ready", "running", "active"}:
        return True
    if state in {"not-loaded", "not_loaded", "unloaded", "stopped"}:
        return False
    for key in ("loaded", "is_loaded", "isLoaded"):
        if key in row:
            return bool(row.get(key))
    instances = row.get("loaded_instances") or row.get("loadedInstances") or row.get("instances")
    return isinstance(instances, list) and bool(instances)


def _request_json(client: httpx.Client, url: str, headers: dict[str, str]) -> tuple[Any, float]:
    started = time.perf_counter()
    response = client.get(url, headers=headers)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    return response.json(), elapsed_ms


def _probe_provider(
    provider: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> NodeInfo:
    base_url = str(provider["base_url"])
    root = _api_root(base_url)
    token_env = str(provider.get("api_token_env") or "LMSTUDIO_API_TOKEN")
    token = os.getenv(token_env, "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    errors: list[str] = []
    all_models: set[str] = set()
    loaded_models: set[str] = set()
    latencies: list[float] = []
    successful_endpoint = False
    inventory_complete = False

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        # Native v1 is authoritative for the downloaded/available inventory.
        try:
            payload, elapsed = _request_json(client, f"{root}/api/v1/models", headers)
            latencies.append(elapsed)
            rows = _inventory_rows(payload)
            for row in rows:
                model_id = _model_id(row)
                if model_id:
                    all_models.add(model_id)
                    if _loaded_from_row(row):
                        loaded_models.add(model_id)
            successful_endpoint = True
            inventory_complete = True
        except Exception as exc:
            errors.append(f"api/v1/models: {type(exc).__name__}: {exc}")

        # v0 exposes explicit loaded/not-loaded state on older and transitional releases.
        try:
            payload, elapsed = _request_json(client, f"{root}/api/v0/models", headers)
            latencies.append(elapsed)
            rows = _inventory_rows(payload)
            for row in rows:
                model_id = _model_id(row)
                if model_id:
                    all_models.add(model_id)
                    if _loaded_from_row(row):
                        loaded_models.add(model_id)
            successful_endpoint = True
            inventory_complete = True
        except Exception as exc:
            errors.append(f"api/v0/models: {type(exc).__name__}: {exc}")

        # OpenAI compatibility is the broadest health fallback. Do not assume every
        # visible model is loaded because JIT mode may expose downloaded models here.
        try:
            payload, elapsed = _request_json(client, f"{root}/v1/models", headers)
            latencies.append(elapsed)
            rows = _inventory_rows(payload)
            visible = {_model_id(row) for row in rows if _model_id(row)}
            all_models.update(visible)
            if not inventory_complete:
                # On older installations this is the only endpoint available. Mark
                # these models loaded so the fleet can recover, but preserve the
                # incomplete flag for downstream caution.
                loaded_models.update(visible)
            successful_endpoint = True
        except Exception as exc:
            errors.append(f"v1/models: {type(exc).__name__}: {exc}")

    configured = {
        str(model.get("provider_model") or "").strip()
        for model in provider.get("models") or []
        if isinstance(model, dict) and str(model.get("provider_model") or "").strip()
    }
    all_models.update(configured)

    return NodeInfo(
        name=str(provider["node_id"]),
        ip=_host(base_url),
        online=successful_endpoint,
        loaded_models=sorted(loaded_models),
        all_models=sorted(all_models),
        latency_ms=round(min(latencies), 3) if latencies else 0.0,
        error="; ".join(errors) if not successful_endpoint else "",
        base_url=base_url,
        provider_name=str(provider.get("name") or ""),
        discovery_source="live",
        inventory_complete=inventory_complete,
    )


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
                base_url=str(row.get("base_url") or ""),
                provider_name=str(row.get("provider_name") or ""),
                discovery_source="cached-report",
                inventory_complete=bool(row.get("inventory_complete")),
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
        entry = by_node.setdefault(node_name, {"loaded_models": set(), "all_models": set()})
        if model_id:
            entry["loaded_models"].add(model_id)
            entry["all_models"].add(model_id)
    return [
        NodeInfo(
            name=name,
            ip="",
            online=False,
            loaded_models=sorted(row["loaded_models"]),
            all_models=sorted(row["all_models"]),
            error="live provider registry unavailable; using stale dispatcher stats",
            discovery_source="cached-stats",
        )
        for name, row in by_node.items()
    ]


def probe_all_nodes(
    *,
    provider_config: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[NodeInfo]:
    """Probe every configured LM Studio runtime.

    Disabled providers are still observed so an operator can see that the node is
    healthy before re-enabling it for routing. Persisted reports are never used when
    a provider registry exists, preventing stale-state feedback loops.
    """

    providers = _provider_rows(provider_config or DEFAULT_PROVIDER_CONFIG)
    if providers:
        nodes = [_probe_provider(provider, timeout_seconds=timeout_seconds) for provider in providers]
        return sorted(nodes, key=lambda node: node.name.lower())

    report_nodes = _from_report(_load_json(DEFAULT_REPORT_PATH))
    if report_nodes:
        return sorted(report_nodes, key=lambda node: node.name.lower())
    return sorted(_from_stats(_load_json(DEFAULT_STATS_PATH)), key=lambda node: node.name.lower())
