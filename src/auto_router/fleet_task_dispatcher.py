from __future__ import annotations

"""Live LM Studio fleet discovery used by loadout rebuilding.

Discovery is deliberately observation-only. The provider registry describes the
nodes that may be observed, while model inventory is accepted only from live
runtime responses. Persisted reports are a diagnostic fallback when the registry
file itself is absent; they never override a present registry.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml

from .offline_guard import host_is_offline_allowed


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
DEFAULT_MAX_WORKERS = int(os.getenv("AUTO_ROUTER_DISCOVERY_MAX_WORKERS", "8"))
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class DiscoveryConfigError(ValueError):
    """Raised when a present provider registry is unsafe or ambiguous."""


@dataclass
class NodeInfo:
    name: str
    ip: str
    online: bool
    loaded_models: list[str] = field(default_factory=list)
    all_models: list[str] = field(default_factory=list)
    configured_models: list[str] = field(default_factory=list)
    model_aliases: dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    endpoint_status: dict[str, str] = field(default_factory=dict)
    power_watts: float = 0.0
    base_url: str = ""
    provider_name: str = ""
    discovery_source: str = "live"
    inventory_complete: bool = False
    inventory_authoritative: bool = False
    loaded_state_source: str = "none"
    observed_at: str = ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
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
    # Match longest prefixes first. ``/api/v1`` also ends in ``/v1``.
    for suffix in ("/api/v1", "/api/v0", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _safe_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", "")).rstrip("/")


def _host(base_url: str) -> str:
    return urlsplit(base_url).hostname or ""


def _validate_base_url(base_url: str, *, provider_name: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DiscoveryConfigError(
            f"provider {provider_name!r} has an invalid LM Studio base_url"
        )
    if parsed.username or parsed.password:
        raise DiscoveryConfigError(
            f"provider {provider_name!r} must use api_token_env instead of URL credentials"
        )
    if not host_is_offline_allowed(parsed.hostname):
        raise DiscoveryConfigError(
            f"provider {provider_name!r} has a public or unresolved LM Studio host"
        )


def _configured_inventory(row: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    configured: set[str] = set()
    aliases: dict[str, str] = {}
    models = row.get("models") or []
    if not isinstance(models, list):
        raise DiscoveryConfigError(
            f"provider {str(row.get('name') or '<unnamed>')!r} models must be a list"
        )
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise DiscoveryConfigError(
                f"provider {str(row.get('name') or '<unnamed>')!r} model #{index} must be a mapping"
            )
        provider_model = str(model.get("provider_model") or "").strip()
        alias = str(model.get("alias") or "").strip()
        if not provider_model:
            raise DiscoveryConfigError(
                f"provider {str(row.get('name') or '<unnamed>')!r} model #{index} "
                "is missing provider_model"
            )
        configured.add(provider_model)
        if alias:
            existing = aliases.get(alias)
            if existing and existing != provider_model:
                raise DiscoveryConfigError(
                    f"provider {str(row.get('name') or '<unnamed>')!r} maps alias "
                    f"{alias!r} to multiple models"
                )
            aliases[alias] = provider_model
    return sorted(configured), dict(sorted(aliases.items()))


def _provider_rows(path: Path = DEFAULT_PROVIDER_CONFIG) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise DiscoveryConfigError(f"cannot read provider registry {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise DiscoveryConfigError(f"provider registry {path} must contain a mapping")
    rows = document.get("providers")
    if not isinstance(rows, list):
        raise DiscoveryConfigError(f"provider registry {path} must contain a providers list")

    providers: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    provider_names: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DiscoveryConfigError(f"provider #{index} must be a mapping")
        if str(row.get("type") or "").lower() != "lmstudio":
            continue
        name = str(row.get("name") or "").strip()
        node_id = str(row.get("node_id") or name).strip()
        base_url = _expand_env(str(row.get("base_url") or "")).strip()
        if not name or not node_id or not base_url:
            raise DiscoveryConfigError(
                f"LM Studio provider #{index} requires name, node_id, and base_url"
            )
        if name in provider_names:
            raise DiscoveryConfigError(f"duplicate LM Studio provider name {name!r}")
        if node_id in node_ids:
            raise DiscoveryConfigError(f"duplicate LM Studio node_id {node_id!r}")
        _validate_base_url(base_url, provider_name=name)
        configured_models, aliases = _configured_inventory(row)
        provider_names.add(name)
        node_ids.add(node_id)
        providers.append(
            {
                **row,
                "name": name,
                "base_url": base_url,
                "node_id": node_id,
                "configured_models": configured_models,
                "model_aliases": aliases,
            }
        )
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
        raise ValueError("response body must be an object")
    rows = payload.get("models")
    if not isinstance(rows, list):
        rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("response body does not contain a models or data list")
    return [row for row in rows if isinstance(row, dict)]


def _has_loaded_state(row: dict[str, Any]) -> bool:
    return any(
        key in row
        for key in (
            "state",
            "status",
            "loaded",
            "is_loaded",
            "isLoaded",
            "loaded_instances",
            "loadedInstances",
            "instances",
        )
    )


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


def _request_json(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    *,
    timeout_seconds: float,
) -> tuple[Any, float]:
    started = time.perf_counter()
    response = client.get(url, headers=headers, timeout=max(timeout_seconds, 0.05))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    return response.json(), elapsed_ms


def _failure_text(endpoint: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{endpoint}: http-{exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return f"{endpoint}: timeout"
    if isinstance(exc, httpx.RequestError):
        return f"{endpoint}: {type(exc).__name__}"
    return f"{endpoint}: {type(exc).__name__}: {exc}"


def _probe_provider(
    provider: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> NodeInfo:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    base_url = str(provider["base_url"])
    root = _api_root(base_url)
    token_env = str(provider.get("api_token_env") or "LMSTUDIO_API_TOKEN")
    token = os.getenv(token_env, "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    configured_models = [str(value) for value in provider.get("configured_models") or []]
    aliases = {
        str(alias): str(model)
        for alias, model in dict(provider.get("model_aliases") or {}).items()
    }

    errors: list[str] = []
    observed_models: set[str] = set()
    loaded_models: set[str] = set()
    latencies: list[float] = []
    endpoint_status: dict[str, str] = {}
    successful_endpoint = False
    native_success = False
    explicit_loaded_state = False
    compatibility_models: set[str] = set()
    deadline = time.monotonic() + timeout_seconds

    endpoints = (
        ("api/v1/models", f"{root}/api/v1/models", True),
        ("api/v0/models", f"{root}/api/v0/models", True),
        ("v1/models", f"{root}/v1/models", False),
    )
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        for endpoint, url, native in endpoints:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                message = f"{endpoint}: skipped (probe budget exhausted)"
                errors.append(message)
                endpoint_status[endpoint] = "skipped:budget-exhausted"
                continue
            try:
                payload, elapsed = _request_json(
                    client,
                    url,
                    headers,
                    timeout_seconds=remaining,
                )
                rows = _inventory_rows(payload)
                latencies.append(elapsed)
                successful_endpoint = True
                endpoint_status[endpoint] = f"ok:{len(rows)}"
                model_ids = {_model_id(row) for row in rows if _model_id(row)}
                observed_models.update(model_ids)
                if native:
                    native_success = True
                    for row in rows:
                        model_id = _model_id(row)
                        if not model_id:
                            continue
                        if _has_loaded_state(row):
                            explicit_loaded_state = True
                            if _loaded_from_row(row):
                                loaded_models.add(model_id)
                else:
                    compatibility_models.update(model_ids)
            except Exception as exc:
                message = _failure_text(endpoint, exc)
                errors.append(message)
                endpoint_status[endpoint] = message.split(": ", 1)[-1]

    loaded_state_source = "none"
    if explicit_loaded_state:
        loaded_state_source = "native"
    elif compatibility_models:
        # The OpenAI-compatible endpoint exposes model visibility, not loaded
        # runtime state. Keep those IDs in all_models and fail closed for routing.
        loaded_state_source = "compatibility-inferred"

    warnings = list(errors) if successful_endpoint else []
    configured_set = set(configured_models)
    missing_configured = sorted(configured_set - observed_models)
    unconfigured = sorted(observed_models - configured_set)
    if successful_endpoint and not observed_models:
        warnings.append("runtime returned an empty model inventory")
    if missing_configured:
        warnings.append("configured models not observed: " + ", ".join(missing_configured))
    if unconfigured:
        warnings.append("observed models not configured for routing: " + ", ".join(unconfigured))
    if loaded_state_source == "compatibility-inferred":
        warnings.append(
            "OpenAI-compatible inventory cannot assert that a model is loaded"
        )

    return NodeInfo(
        name=str(provider["node_id"]),
        ip=_host(base_url),
        online=successful_endpoint,
        loaded_models=sorted(loaded_models),
        all_models=sorted(observed_models),
        configured_models=sorted(configured_models),
        model_aliases=dict(sorted(aliases.items())),
        latency_ms=round(min(latencies), 3) if latencies else 0.0,
        error="; ".join(errors) if not successful_endpoint else "",
        warnings=warnings,
        endpoint_status=endpoint_status,
        base_url=_safe_base_url(base_url),
        provider_name=str(provider.get("name") or ""),
        discovery_source="live",
        inventory_complete=native_success,
        inventory_authoritative=native_success and explicit_loaded_state,
        loaded_state_source=loaded_state_source,
        observed_at=datetime.now(UTC).isoformat(),
    )


def _from_report(report: dict[str, Any]) -> list[NodeInfo]:
    nodes: list[NodeInfo] = []
    for row in report.get("nodes") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        historical_models = {
            str(model)
            for model in (
                list(row.get("all_models") or [])
                + list(row.get("loaded_models") or [])
            )
            if str(model)
        }
        nodes.append(
            NodeInfo(
                name=name,
                ip=str(row.get("ip") or ""),
                online=bool(row.get("online")),
                loaded_models=[],
                all_models=sorted(historical_models),
                configured_models=[
                    str(model) for model in row.get("configured_models") or []
                ],
                model_aliases={
                    str(alias): str(model)
                    for alias, model in dict(row.get("model_aliases") or {}).items()
                },
                latency_ms=float(row.get("latency_ms") or 0.0),
                error=str(row.get("error") or ""),
                warnings=[
                    *[str(value) for value in row.get("warnings") or []],
                    "cached report inventory is diagnostic-only",
                ],
                endpoint_status={
                    str(endpoint): str(status)
                    for endpoint, status in dict(row.get("endpoint_status") or {}).items()
                },
                power_watts=float(row.get("power_watts") or 0.0),
                base_url=str(row.get("base_url") or ""),
                provider_name=str(row.get("provider_name") or ""),
                discovery_source="cached-report",
                inventory_complete=bool(row.get("inventory_complete")),
                inventory_authoritative=False,
                loaded_state_source="cached",
                observed_at=str(row.get("observed_at") or ""),
            )
        )
    return nodes


def _from_stats(stats: dict[str, Any]) -> list[NodeInfo]:
    by_node: dict[str, set[str]] = {}
    for row in stats.get("slots") or []:
        if not isinstance(row, dict):
            continue
        node_name = str(row.get("node") or "").strip()
        model_id = str(row.get("model") or "").strip()
        if not node_name:
            continue
        models = by_node.setdefault(node_name, set())
        if model_id:
            models.add(model_id)
    return [
        NodeInfo(
            name=name,
            ip="",
            online=False,
            loaded_models=[],
            all_models=sorted(models),
            error="live provider registry unavailable; using stale dispatcher stats",
            warnings=["cached dispatcher statistics are diagnostic-only"],
            discovery_source="cached-stats",
            inventory_authoritative=False,
            loaded_state_source="cached",
        )
        for name, models in by_node.items()
    ]


def _unexpected_probe_failure(provider: dict[str, Any], exc: Exception) -> NodeInfo:
    base_url = str(provider.get("base_url") or "")
    return NodeInfo(
        name=str(provider.get("node_id") or provider.get("name") or "unknown"),
        ip=_host(base_url),
        online=False,
        configured_models=[str(value) for value in provider.get("configured_models") or []],
        model_aliases={
            str(alias): str(model)
            for alias, model in dict(provider.get("model_aliases") or {}).items()
        },
        error=f"unexpected probe failure: {type(exc).__name__}: {exc}",
        base_url=_safe_base_url(base_url) if base_url else "",
        provider_name=str(provider.get("name") or ""),
        discovery_source="live",
        observed_at=datetime.now(UTC).isoformat(),
    )


def probe_all_nodes(
    *,
    provider_config: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[NodeInfo]:
    """Probe every configured LM Studio runtime within a bounded per-node budget.

    A present registry is always authoritative about which nodes exist, including
    when it contains zero LM Studio providers. Cached reports are used only when
    the registry file is absent and are always marked non-authoritative.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if max_workers <= 0:
        raise ValueError("max_workers must be greater than zero")

    path = provider_config or DEFAULT_PROVIDER_CONFIG
    registry_exists = path.exists()
    providers = _provider_rows(path)
    if registry_exists:
        if not providers:
            return []
        workers = min(max_workers, len(providers))
        nodes: list[NodeInfo] = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fleet-discovery") as pool:
            futures = {
                pool.submit(
                    _probe_provider,
                    provider,
                    timeout_seconds=timeout_seconds,
                ): provider
                for provider in providers
            }
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    nodes.append(future.result())
                except Exception as exc:
                    nodes.append(_unexpected_probe_failure(provider, exc))
        return sorted(nodes, key=lambda node: node.name.lower())

    report_nodes = _from_report(_load_json(DEFAULT_REPORT_PATH))
    if report_nodes:
        return sorted(report_nodes, key=lambda node: node.name.lower())
    return sorted(_from_stats(_load_json(DEFAULT_STATS_PATH)), key=lambda node: node.name.lower())
