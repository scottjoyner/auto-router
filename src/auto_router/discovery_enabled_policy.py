"""Install enabled-state semantics for live LM Studio discovery.

The original discovery parser predated the reconciled rollout controls and did
not honor ``enabled: false``. This policy keeps disabled providers in the source
registry for operator history while excluding them from validation, probing,
and authoritative inventory.
"""

from __future__ import annotations

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

    discovery._provider_rows = active_provider_rows
    _INSTALLED = True
