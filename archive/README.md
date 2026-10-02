# archive/ — non-loadable historical configuration

**Status: ARCHIVE_ONLY. Nothing in this directory is read by any code path, at
any generation, in any entrypoint.**

This directory holds pre-strict-offline configuration snapshots retained as
provenance. They are **not** fallbacks.

## Proven non-loadability

Verified by exhaustive reference search and by an executable contract test
(`tests/test_housekeeping_config_authority.py`):

| Artifact | Referenced by code / config / docs | Loaded by |
|---|---|---|
| `context.yaml.static-fallback` | nothing | nothing |
| `context.yaml.static-fallback.note` | nothing | nothing |
| `providers.yaml.bak` | nothing | nothing |

Evidence:

1. `grep -rIn "archive" . --exclude-dir=.git` matches only the note file's own
   first line. No loader, compose file, Makefile target, script, or document
   names this directory.
2. No code performs glob discovery over `config/` or the repository root, so no
   `.bak` / `.static-fallback` file can be picked up implicitly.
3. `tests/test_housekeeping_config_authority.py::test_canonical_config_loading_never_opens_archived_artifacts`
   instruments `pathlib.Path.open` while the four canonical loaders run and
   asserts that no archived path is ever opened.
4. `providers.yaml.bak` contains **enabled public-inference providers**
   (`groq`, `cerebras`, `openrouter`). Even if it were pointed at directly, the
   strict-offline guard rejects it — see
   `test_archived_hosted_provider_registry_is_rejected_by_offline_guard`.

## Why these files must not be restored

- `providers.yaml.bak` violates the strict-offline contract
  (`quota_class: fast_free` / `brokered_free`, hosted `base_url` hosts). Restoring
  it would re-admit public inference providers and break the local-only privacy
  wall.
- `context.yaml.static-fallback` is a snapshot of the retired static fleet. The
  canonical fleet/context truth is owned by AssistX/Neo4j and projected over HTTP
  via `AUTO_ROUTER_CONTEXT_CONFIG`.

## Current behaviour when canonical configuration is missing

Missing canonical configuration **fails clearly; it does not fall back here.**

- `AUTO_ROUTER_PROVIDER_CONFIG` missing → `enforce_strict_offline_provider_config()`
  raises `RuntimeError` at import of `auto_router.main_live` /
  `auto_router.secure_live`.
- `AUTO_ROUTER_CONTEXT_CONFIG` missing → the snapshot is labelled
  `projection_status: bootstrap`, `projection_loaded: false`, and is rebuilt from
  the provider/agent-worker registries only. See
  `test_missing_canonical_context_degrades_to_labelled_bootstrap`.

## Do not

- point `AUTO_ROUTER_*_CONFIG` at a path under `archive/`;
- add a "restore from archive" fallback to any loader;
- delete these files as part of a cleanup slice without an operator decision —
  they are the provenance record for the W-58 static-context removal.
