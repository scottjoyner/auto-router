from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_PROVIDER_TYPES = {
    "lmstudio",
    "llama_cpp",
    "llamacpp",
    "openai_compatible",
    "sglang",
    "vllm",
}
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def strict_offline_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("AUTO_ROUTER_STRICT_OFFLINE", "true")).strip().lower() in _TRUE_VALUES


def _expand_env(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2) or ""
            return str(env.get(name, default))

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item, env) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item, env) for key, item in value.items()}
    return value


def _explicit_allowed_hosts(env: Mapping[str, str]) -> set[str]:
    return {
        item.strip().lower().rstrip(".")
        for item in str(env.get("AUTO_ROUTER_OFFLINE_ALLOWED_HOSTS", "")).split(",")
        if item.strip()
    }


def host_is_offline_allowed(host: str, env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    normalized = host.strip().lower().strip("[]").rstrip(".")
    if not normalized:
        return False
    if normalized in _explicit_allowed_hosts(source):
        return True
    if normalized in {"localhost", "host.docker.internal", "gateway.docker.internal"}:
        return True
    if normalized.endswith((".lan", ".local", ".internal", ".ts.net")):
        return True
    # Docker Compose and MagicDNS service names are commonly single labels.
    if "." not in normalized and ":" not in normalized:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address in _TAILSCALE_CGNAT
    )


def _validate_access_url(name: str, label: str, value: Any, env: Mapping[str, str]) -> list[str]:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return [f"{name}: {label} must be a valid http(s) URL"]
    if not host_is_offline_allowed(parsed.hostname, env):
        return [
            f"{name}: public or unresolved provider host '{parsed.hostname}' is forbidden"
        ]
    return []


def validate_offline_provider_config(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    source = env if env is not None else os.environ
    config_path = Path(path)
    if not config_path.exists():
        return [f"provider config does not exist: {config_path}"]

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = _expand_env(yaml.safe_load(handle) or {}, source)

    providers = loaded.get("providers", []) if isinstance(loaded, dict) else []
    if not isinstance(providers, list):
        return ["provider config field 'providers' must be a list"]

    errors: list[str] = []
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            errors.append(f"providers[{index}] must be an object")
            continue
        if not bool(provider.get("enabled", True)):
            continue

        name = str(provider.get("id") or provider.get("name") or f"providers[{index}]")
        provider_type = str(provider.get("type") or "").strip().lower()
        quota_class = str(provider.get("quota_class") or "local").strip().lower()

        if provider_type not in _ALLOWED_PROVIDER_TYPES:
            errors.append(f"{name}: provider type '{provider_type}' is not allowed offline")
        if quota_class != "local":
            errors.append(f"{name}: quota_class must be 'local', got '{quota_class}'")
        if bool(provider.get("gateway_managed", False)):
            errors.append(f"{name}: gateway_managed providers are forbidden in strict offline mode")

        errors.extend(_validate_access_url(name, "base_url", provider.get("base_url"), source))
        access_urls = provider.get("access_urls") or []
        if not isinstance(access_urls, list):
            errors.append(f"{name}: access_urls must be a list")
        else:
            seen: set[str] = set()
            for access_index, access_url in enumerate(access_urls):
                normalized = str(access_url or "").strip().rstrip("/")
                if not normalized:
                    continue
                if normalized in seen:
                    errors.append(f"{name}: duplicate access_urls entry '{normalized}'")
                    continue
                seen.add(normalized)
                errors.extend(
                    _validate_access_url(
                        name,
                        f"access_urls[{access_index}]",
                        access_url,
                        source,
                    )
                )

        try:
            slots = int(provider.get("parallel_slots") or 0)
        except (TypeError, ValueError):
            slots = -1
            errors.append(f"{name}: parallel_slots must be an integer")
        runtime_instance_id = str(provider.get("runtime_instance_id") or "").strip()
        if slots > 0 and not runtime_instance_id:
            errors.append(
                f"{name}: runtime_instance_id is required when parallel_slots is positive"
            )

    if not any(isinstance(item, dict) and bool(item.get("enabled", True)) for item in providers):
        errors.append("strict offline mode requires at least one enabled local provider")
    return errors


def enforce_strict_offline_provider_config(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    source = env if env is not None else os.environ
    if not strict_offline_enabled(source):
        return
    config_path = path or source.get("AUTO_ROUTER_PROVIDER_CONFIG", "config/providers.yaml")
    errors = validate_offline_provider_config(config_path, env=source)
    if errors:
        details = "; ".join(errors)
        raise RuntimeError(f"strict offline provider validation failed: {details}")
