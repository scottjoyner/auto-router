"""Contract tests for the canonical Compose posture.

These pin the environment-level guards that the code alone cannot enforce, so a
deployment cannot drift back into a fleet-mutating or admission-widening posture:

* ``AUTO_ROUTER_DISCOVER_TAILNET_LMSTUDIO`` is ``"0"`` everywhere. The container
  mounts the tailscale socket and CLI, so leaving discovery on lets
  ``merge_discovered_lmstudio_providers()`` re-enable an operator-disabled provider
  and overwrite its ``base_url`` / ``priority`` from observation alone.
* ``AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED`` is ``"false"`` everywhere, so
  ``POST /admin/load?apply=true`` cannot load models on its own authority.
* ``AUTO_ROUTER_AUTOLOAD_ENABLED`` / ``AUTO_ROUTER_PLACEMENT_UNLOAD`` stay false.
* ``AUTO_ROUTER_STRICT_OFFLINE`` stays true.
* The gateway overlays stay disabled.

Interpolation is resolved the same way Compose resolves it, so ``${VAR:-default}``
forms are evaluated rather than compared literally.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml


def _compose_passthrough(loader, tag_suffix, node):
    """Reduce a ``!override`` / ``!reset`` tagged node to its plain value."""

    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose merge tags.

    The overlays use ``!override`` (Compose >= 2.24.4). For this test the tag is
    irrelevant -- the files are read for their declared environment mappings --
    so it is reduced to an ordinary value.
    """


_ComposeLoader.add_multi_constructor("!", _compose_passthrough)

REPO_ROOT = Path(__file__).resolve().parents[1]

CANONICAL_COMPOSE = (
    "docker-compose.yml",
    "docker-compose.integration.yml",
    "compose.reconciliation.yml",
)

_ENV_LINE = re.compile(r"^\s{6}(AUTO_ROUTER_[A-Z0-9_]+):\s*(.*)$")


def _own_service_env(relative_path: str) -> dict[str, str]:
    """The environment a single compose file declares for ``llm-router`` itself."""

    document = yaml.load((REPO_ROOT / relative_path).read_text(encoding="utf-8"), Loader=_ComposeLoader)
    service = document["services"]["llm-router"]
    resolved: dict[str, str] = {}
    for key, value in service.get("environment", {}).items():
        if value is None:
            resolved[key] = ""
        elif isinstance(value, bool):
            resolved[key] = "true" if value else "false"
        else:
            resolved[key] = _interpolate(str(value))
    return resolved


def _service_env(relative_path: str) -> dict[str, str]:
    """Resolve the effective environment the way Compose merges the files.

    The two overlays are only ever applied *on top of* ``docker-compose.yml``, and
    Compose merges ``environment:`` mappings with the overlay winning. Reading an
    overlay in isolation would miss every inherited value, so merge explicitly.
    """

    base = _own_service_env("docker-compose.yml")
    if relative_path == "docker-compose.yml":
        return base
    merged = dict(base)
    merged.update(_own_service_env(relative_path))
    return merged


def _interpolate(value: str) -> str:
    """Resolve ``${VAR}`` / ``${VAR:-default}`` against the process environment."""

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        return os.environ.get(name, default if default is not None else "")

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", replace, value)


@pytest.mark.parametrize("relative_path", CANONICAL_COMPOSE)
def test_tailnet_discovery_is_disabled(relative_path: str) -> None:
    env = _service_env(relative_path)
    assert env.get("AUTO_ROUTER_DISCOVER_TAILNET_LMSTUDIO") == "0", (
        f"{relative_path} must not enable tailnet LM Studio discovery"
    )


@pytest.mark.parametrize("relative_path", CANONICAL_COMPOSE)
def test_manual_model_apply_is_disabled(relative_path: str) -> None:
    env = _service_env(relative_path)
    assert env.get("AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED") == "false", (
        f"{relative_path} must not enable manual model loading"
    )


@pytest.mark.parametrize("relative_path", CANONICAL_COMPOSE)
def test_autoload_and_unload_stay_off(relative_path: str) -> None:
    env = _service_env(relative_path)
    assert env.get("AUTO_ROUTER_AUTOLOAD_ENABLED") == "false"
    assert env.get("AUTO_ROUTER_PLACEMENT_UNLOAD") == "false"


@pytest.mark.parametrize("relative_path", CANONICAL_COMPOSE)
def test_strict_offline_stays_on(relative_path: str) -> None:
    assert _service_env(relative_path).get("AUTO_ROUTER_STRICT_OFFLINE") == "true"


@pytest.mark.parametrize("relative_path", CANONICAL_COMPOSE)
def test_agentgateway_stays_off(relative_path: str) -> None:
    assert _service_env(relative_path).get("AUTO_ROUTER_AGENTGATEWAY_ENABLED") == "false"


@pytest.mark.parametrize("relative_path", CANONICAL_COMPOSE)
def test_prompt_logging_stays_off(relative_path: str) -> None:
    assert _service_env(relative_path).get("AUTO_ROUTER_LOG_PROMPTS") == "false"


def test_env_example_documents_the_new_guards() -> None:
    """An operator reading .env.example must be able to discover these knobs."""

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED" in text
    assert "AUTO_ROUTER_DISCOVER_TAILNET_LMSTUDIO" in text


def test_agentgateway_overlay_is_marked_superseded() -> None:
    text = (REPO_ROOT / "docker-compose.agentgateway.yml").read_text(encoding="utf-8")
    assert "STATUS: SUPERSEDED" in text


def test_every_entrypoint_enforces_the_offline_provider_guard() -> None:
    """X5: the guard must run for `auto_router.main:app`, not just main_live.

    `main_live` and `secure_live` enforce it at import time. `main.load_state`
    now enforces it too, so `uvicorn auto_router.main:app` and
    `python -m auto_router.main` cannot load a hosted provider registry such as
    `archive/providers.yaml.bak`.
    """

    import asyncio
    import os
    from pathlib import Path

    import auto_router.main as main_module

    repo = Path(__file__).resolve().parents[1]
    original = {
        key: os.environ.get(key)
        for key in (
            "AUTO_ROUTER_STRICT_OFFLINE",
            "AUTO_ROUTER_PROVIDER_CONFIG",
            "AUTO_ROUTER_CONTEXT_CONFIG",
        )
    }
    try:
        os.environ["AUTO_ROUTER_STRICT_OFFLINE"] = "true"
        os.environ["AUTO_ROUTER_PROVIDER_CONFIG"] = str(repo / "archive" / "providers.yaml.bak")
        os.environ["AUTO_ROUTER_CONTEXT_CONFIG"] = str(
            repo / "config" / "context.example.yaml"
        )
        from auto_router.settings import get_settings

        get_settings.cache_clear()

        with pytest.raises(RuntimeError, match="strict offline provider validation failed"):
            asyncio.run(main_module.load_state())
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from auto_router.settings import get_settings

        get_settings.cache_clear()


def test_load_state_accepts_the_canonical_registries() -> None:
    """The guard must not reject the registries this deployment actually ships."""

    from auto_router.offline_guard import validate_offline_provider_config

    for name in ("providers.yaml", "providers.reconciliation.yaml", "providers.example.yaml"):
        assert validate_offline_provider_config(REPO_ROOT / "config" / name, env={}) == [], name
