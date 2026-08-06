"""Install enabled-state semantics for live LM Studio discovery.

The legacy registry parser remains available for configuration diagnostics, but
``probe_all_nodes`` uses only active providers. Disabled entries stay in the
source registry for operator history while being excluded from validation,
network access, and authoritative inventory.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_INSTALLED = False


def _is_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _FALSE_VALUES


def install_enabled_discovery_policy() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from . import fleet_task_dispatcher as discovery

    def active_provider_rows(
        path: Path = discovery.DEFAULT_PROVIDER_CONFIG,
    ) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise discovery.DiscoveryConfigError(
                f"cannot read provider registry {path}: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise discovery.DiscoveryConfigError(
                f"provider registry {path} must contain a mapping"
            )
        rows = document.get("providers")
        if not isinstance(rows, list):
            raise discovery.DiscoveryConfigError(
                f"provider registry {path} must contain a providers list"
            )

        providers: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        provider_names: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise discovery.DiscoveryConfigError(
                    f"provider #{index} must be a mapping"
                )
            if str(row.get("type") or "").lower() != "lmstudio":
                continue
            # Disabled entries are not active discovery configuration. Skip them
            # before URL, model, or duplicate validation so a drained historical
            # entry cannot block or re-enter the live fleet.
            if not _is_enabled(row.get("enabled", True)):
                continue

            name = str(row.get("name") or "").strip()
            node_id = str(row.get("node_id") or name).strip()
            base_url = discovery._expand_env(
                str(row.get("base_url") or "")
            ).strip()
            if not name or not node_id or not base_url:
                raise discovery.DiscoveryConfigError(
                    f"LM Studio provider #{index} requires name, node_id, and base_url"
                )
            if name in provider_names:
                raise discovery.DiscoveryConfigError(
                    f"duplicate LM Studio provider name {name!r}"
                )
            if node_id in node_ids:
                raise discovery.DiscoveryConfigError(
                    f"duplicate LM Studio node_id {node_id!r}"
                )
            discovery._validate_base_url(base_url, provider_name=name)
            configured_models, aliases = discovery._configured_inventory(row)
            provider_names.add(name)
            node_ids.add(node_id)
            providers.append(
                {
                    **row,
                    "enabled": True,
                    "name": name,
                    "base_url": base_url,
                    "node_id": node_id,
                    "configured_models": configured_models,
                    "model_aliases": aliases,
                }
            )
        return providers

    def active_probe_all_nodes(
        *,
        provider_config: Path | None = None,
        timeout_seconds: float = discovery.DEFAULT_TIMEOUT_SECONDS,
        max_workers: int = discovery.DEFAULT_MAX_WORKERS,
    ) -> list[Any]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than zero")

        path = provider_config or discovery.DEFAULT_PROVIDER_CONFIG
        registry_exists = path.exists()
        providers = active_provider_rows(path)
        if registry_exists:
            if not providers:
                return []
            workers = min(max_workers, len(providers))
            nodes: list[Any] = []
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="fleet-discovery",
            ) as pool:
                futures = {
                    pool.submit(
                        discovery._probe_provider,
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
                        nodes.append(
                            discovery._unexpected_probe_failure(provider, exc)
                        )
            return sorted(nodes, key=lambda node: node.name.lower())

        report_nodes = discovery._from_report(
            discovery._load_json(discovery.DEFAULT_REPORT_PATH)
        )
        if report_nodes:
            return sorted(report_nodes, key=lambda node: node.name.lower())
        return sorted(
            discovery._from_stats(
                discovery._load_json(discovery.DEFAULT_STATS_PATH)
            ),
            key=lambda node: node.name.lower(),
        )

    discovery.probe_all_nodes = active_probe_all_nodes
    _INSTALLED = True
