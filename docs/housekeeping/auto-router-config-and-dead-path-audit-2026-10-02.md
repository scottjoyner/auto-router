# auto-router configuration & dead-path audit — 2026-10-02

Static repository-housekeeping campaign. **No running fleet, Docker/network state,
production service, AssistX/Neo4j instance, routing authority, admission policy,
dispatch authority, or provider credential was read or modified.** All findings
come from source, committed configuration, and mocked/local unit tests.

| | |
|---|---|
| Repository | `scottjoyner/auto-router` |
| Base commit | `034d108` — *Add lmstudio-destroyer-k2 provider* |
| Branch | `housekeeping/config-dead-path-audit-2026-10-02` |
| Slice 1 | `c7d6f57` — audit, archive-unreachability proof, two fail-closed fixes |
| Slice 2 | `2f939c3` — duplication consolidation, CI repair, preflight drift |
| Slice 3 | this commit — S1 blast-radius measurement + corrections to this audit |
| Production entrypoint | `auto_router.secure_live:app` (Dockerfile `CMD`) |
| Test command | `bash scripts/test_reconciliation.sh` (blocking CI suite) |

> ### ⚠ Concurrent change on `main` after this branch was cut
>
> While slices 1 and 2 were in progress, a separate automated actor
> (**`Remediation Bot <remediation@local>`**) committed `d3a9258` — *pin router
> projections to canonical AssistX DNS* — directly onto `main`. That commit was
> **not** made by this campaign and has **not** been modified, reverted, or merged
> here. This branch is based on `034d108` and therefore does **not** contain
> `d3a9258`.
>
> `d3a9258` touches **only** `docker-compose.yml`, and it materially changes three
> findings below. Read §14 before acting on C4, C8, S4/X3, or the Compose volume
> map.

---

## 1. Configuration authority map

`runtime reader(s)` = the code that can actually read the file.
`active/current` = reachable from the production entrypoint `secure_live:app`.

| file | runtime reader(s) | active/current? | fallback? | generated? | operator-editable? | test-only? | superseded? | risk if removed |
|---|---|---|---|---|---|---|---|---|
| `config/providers.yaml` | `settings.provider_config` → `config.load_provider_registry` (`main.load_state`, `main_live`); `offline_guard.enforce_strict_offline_provider_config` at import of `main_live`/`secure_live`; `fleet_task_dispatcher.DEFAULT_PROVIDER_CONFIG` (env `AUTO_ROUTER_PROVIDER_CONFIG`) | **yes** — canonical fleet registry | no | no | yes | no | no | **fatal** — `enforce_strict_offline_provider_config` raises at import; router cannot boot |
| `config/providers.reconciliation.yaml` | same readers, selected by env in `compose.reconciliation.yml` + `docker-compose.integration.yml` | **yes** — shadow/smoke bootstrap | no | no | yes | partly (integration smoke) | no | fatal for reconciliation + integration compose |
| `config/providers.example.yaml` | **none at runtime** (validated only by the offline guard in this audit's tests) | no | no | no | yes | example | **yes** — omits every real node | low (documentation only) |
| `config/policies.yaml` | `settings.policy_config` → `config.load_policy_registry`; `PolicyEngine` | **yes** — canonical policy profiles | no | no | yes | no | no | **high** — empty `profiles` ⇒ every route decision fails to match |
| `config/policies.example.yaml` | none | no | no | no | yes | example | **yes** — still names `flash_start_planner` / `backlog_burn` | low, but see finding **D2** |
| `config/agent_workers.yaml` | `settings.agent_config` → `config.load_agent_worker_registry`; `AgentJobManager`; `config._node_context` (bootstrap context nodes) | **yes** — canonical worker registry | no | no | yes | no | no | medium — agent jobs + bootstrap nodes disappear |
| `config/agent_workers.example.yaml` | none | no | no | no | yes | example | byte-identical to `agent_workers.yaml` | none |
| `config/context.yaml` | `settings.context_config` → `config.load_context_snapshot_async` | **yes** *only when `AUTO_ROUTER_CONTEXT_CONFIG` is a local path* — every canonical compose file sets it to an **http(s)** AssistX projection instead | no | no | yes | no | **yes** (W-58 removed the static fleet snapshot as truth) | medium — router still boots, but with `projection_status: bootstrap` |
| `config/context.example.yaml` | none in production; `AUTO_ROUTER_CONTEXT_CONFIG` in `docker-compose.integration.yml` | smoke only | no | no | yes | yes (integration smoke) | byte-identical to `config/context.yaml` | n/a |
| `config/agentgateway.gateway.example.yaml` | none | no | no | no | yes | no | **yes** | none |
| `config/agentgateway.local.example.yaml` | mounted as *live* gateway config by `docker-compose.agentgateway.yml` | only via the SUPERSEDED agentgateway overlay | no | no | — | no | **yes** | none on the current deployment |
| `config/agentgateway.routes.example.yaml` | none | no | no | no | yes | no | **yes** | none |
| `archive/context.yaml.static-fallback` | **nothing** (exhaustively proven, §5) | **no** | no | no | no | no | **yes** | none |
| `archive/context.yaml.static-fallback.note` | **nothing** | no | no | no | no | no | n/a | none |
| `archive/providers.yaml.bak` | **nothing** | **no** | no | no | no | no | **yes** | none — and it is *rejected* by the offline guard if pointed at directly |
| `docker-compose.yml` | `docker compose` (host) | **yes** — canonical production compose | no | no | yes | no | no | fatal for deployment |
| `docker-compose.integration.yml` | `docker compose` overlay; CI `integration-smoke` job | CI only | no | no | yes | **yes** | no | CI smoke only |
| `compose.reconciliation.yml` | `docker compose` overlay; `Makefile` `RECON_COMPOSE` | **yes** — shadow router | no | no | yes | no | no | fatal for shadow router |
| `docker-compose.agentgateway.yml` | `docker compose` overlay | **no** — `gateway-*` Makefile targets exit 2 | no | no | yes | no | **yes** | none |
| `examples/docker-compose.tailscale-sidecar.yml` | none (reference topology, `docs/TAILSCALE_DOCKER.md`) | no | no | no | yes | no | no | none |

### Classification

- **Canonical**: `config/providers.yaml`, `config/providers.reconciliation.yaml`,
  `config/policies.yaml`, `config/agent_workers.yaml`, `docker-compose.yml`,
  `compose.reconciliation.yml`.
- **Compatibility**: `config/context.yaml` / `config/context.example.yaml`
  (retained local-file form of a projection that is HTTP-sourced in every
  canonical deployment).
- **Archive-only**: all three `archive/` artifacts (proven unloadable).
- **Example-only, no runtime reader**: `config/providers.example.yaml`,
  `config/policies.example.yaml`, `config/agentgateway.*.example.yaml`.
- **Unknown**: none. Every file in `config/`, `archive/`, and the repo root was
  classified.

---

## 2. Configuration loaders

### 2.1 Entry points and precedence

| Layer | Mechanism | Precedence |
|---|---|---|
| Runtime paths | `Settings` (`pydantic-settings`, `env_prefix=AUTO_ROUTER_`, `env_file=".env"`) | env > `.env` > field default |
| Field defaults | `config/providers.yaml`, `config/policies.yaml`, `config/agent_workers.yaml`, `config/context.yaml` | **cwd-relative** |
| Env indirection | `fleet_task_dispatcher.DEFAULT_PROVIDER_CONFIG` reads `AUTO_ROUTER_PROVIDER_CONFIG` **itself** | same env var, resolved at module import |
| `gateway_config.GatewayConfig.from_env()` | reads `AUTO_ROUTER_AGENTGATEWAY_*` via raw `os.getenv` | env only, no `.env` file |

Findings:

- **L1 — cwd-dependent defaults.** All four `Settings` path defaults are relative
  (`config/...`). Correct in Docker (`WORKDIR /app`) and when running from the
  repo root; a different cwd yields a silently empty registry. Mitigated for the
  production entrypoint because `main_live` passes
  `AUTO_ROUTER_PROVIDER_CONFIG` absolute in every canonical compose file, and
  `enforce_strict_offline_provider_config` fails closed on the provider registry.
  `policies`/`agent_workers`/`context` have no equivalent guard.
- **L2 — dual reader of the same env var.** `Settings.provider_config` and
  `fleet_task_dispatcher.DEFAULT_PROVIDER_CONFIG` independently read
  `AUTO_ROUTER_PROVIDER_CONFIG`. `DEFAULT_PROVIDER_CONFIG` is evaluated at module
  import, so `monkeypatch.setenv` after import does not move it. Tests must pass
  `provider_config=` explicitly (see `tests/test_fleet_live_discovery.py`).
- **L3 — existence-based selection in `probe_all_nodes`.** When the provider
  registry file is **absent**, `fleet_task_dispatcher.probe_all_nodes` falls back to
  `data/fleet_loadout_report.json` then `data/fleet_dispatcher_stats.json`.
  `discovery_enabled_policy.install_enabled_discovery_policy` (installed from
  `auto_router/__init__.py`, i.e. on any import of the package) preserves this
  fallback. This is the only implicit config fallback left in the repository, it is
  scoped to a diagnostic loadout-rebuild path, and a *present* registry always
  wins (including an empty one). Documented, not changed.
- **L4 — silent defaults in `load_yaml`.** `load_yaml(path, default)` returns
  `default` for a missing file with no error. `load_provider_registry` therefore
  returns an empty registry rather than failing. Combined with the `WARNING:` print
  in `main.load_state`, a missing registry under `auto_router.main:app` yields
  503-on-every-request (fail-closed for routing, but not fail-clear). Under
  `main_live`/`secure_live` the import-time `enforce_strict_offline_provider_config`
  raises first, so the production path is fail-closed.
- **L5 — RESOLVED (slice 2) — triplicated `${VAR:-default}` expansion.**
  `config._expand_env` (`\$\{([^}:]+)(?::-([^}]*))?\}`), `offline_guard._expand_env`
  (same regex, explicit `env` mapping), and `fleet_task_dispatcher._expand_env`
  (`\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}`, string-only) now all delegate to
  `auto_router.env_expansion.expand_env`, which keeps the **stricter**
  shell-identifier pattern. The dispatcher previously rejected non-identifier names
  the other two accepted, so the same YAML could expand differently per reader.
  Unifying on the strict pattern can only *narrow* substitution.
  Safety was established before the change: every `${...}` token in `config/` and
  `archive/` uses an identifier-shaped name, and
  `tests/test_env_expansion.py::test_all_committed_tokens_use_identifier_safe_names`
  now enforces that permanently (and was confirmed to fail when a `${bad-name}`
  token is introduced).
- **L6 — RESOLVED (slice 2) — six copies of SQLite URL → path.** `ledger`,
  `service_store`, `memory_store`, `signal_registry`, `model_registry`, **and
  `event_outbox`** (a sixth copy the first pass of this audit under-counted) each
  carried an identical `_path_from_url`. All six now call
  `auto_router.sqlite_path.sqlite_path`.
  `tests/test_sqlite_path.py` keeps the pre-consolidation algorithm verbatim as a
  reference implementation and asserts string-form equality for every URL form,
  including the two preserved quirks: `sqlite:////x` renders as `//x` (POSIX keeps
  the leading double slash) and an unrecognised URL is handed to `Path()` verbatim.
  `Settings.latency_cache_path` is deliberately **not** folded in — it derives a
  sibling filename and has its own absolute/cwd-relative handling, so sharing it
  would change behaviour rather than deduplicate it.
- **L7 — `Settings` has no `model_config` guard against unknown env keys** — it uses
  `extra="ignore"`, which is intended. However `ModelConfig`/`ProviderConfig` also
  default to pydantic `extra="ignore"`, so **unknown provider keys are silently
  dropped** (see **D4**).

### 2.2 Ambiguities resolved with focused tests

`tests/test_housekeeping_config_authority.py` (new) pins:

1. The four canonical loaders never open anything under `archive/` (instruments
   `pathlib.Path.open`).
2. Archived basenames do not exist in `config/`.
3. `archive/providers.yaml.bak` is rejected by `validate_offline_provider_config`.
4. All three canonical provider registries pass strict-offline validation.
5. A missing context configuration produces a **labelled** bootstrap snapshot
   (`projection_status == "bootstrap"`, `projection_loaded is False`) built from
   the provider registry only.
6. `main_live` resolves the schema-v1 HMAC manager; `secure_live` resolves the
   schema-v2 Ed25519 manager. Each entrypoint is imported in a **fresh
   subprocess**, because `secure_live`'s rebinding is an import-time module side
   effect that would otherwise make the assertions depend on test collection
   order.

---

## 3. Runtime-projection generation reconciliation

### 3.1 Disposition

| | `runtime_projection.py` (v1) | `runtime_projection_v2.py` (v2) |
|---|---|---|
| Schema | `schema_version: "1"`, HMAC-SHA256 over `generation:checksum:generated_at:expires_at` | `schema_version: "2"`, Ed25519 signature over a canonical JSON payload |
| Signature key | `AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET` (empty ⇒ reject) | `AUTO_ROUTER_RUNTIME_PROJECTION_VERIFY_KEY_FILE` / `_PEM` (empty ⇒ reject) |
| Contains swap mechanics | **yes** — `RuntimeGeneration`, `RuntimeAdmissionController`, `RuntimeAccessPathSelector`, `apply()` state replacement, `projection_poll_task` | no — delegates via `super().apply(converted)` |
| Called in production | **yes, as a base class** | **yes** — `secure_live.py:20` rebinds `main_live_module.RuntimeProjectionManager` |
| Called by API/routes | `main_live` module global (rebinding target) | `/admin/runtime-projection` GET + POST, `/admin/admission` |
| Called by tests only | `tests/test_runtime_projection.py` (schema-v1 migration/negative cases) | `tests/test_runtime_projection_v2.py` |
| Relationship | base | **subclass** |

### 3.2 Can one silently bypass the other?

**Yes — and this is the single most important structural finding in the audit.**

`main_live.py:44` imports the **v1** `RuntimeProjectionManager`. `main_live`'s
lifespan constructs the manager by reading the *module global* at line 240, so the
rebinding performed by `secure_live.py:20` (`main_live_module.RuntimeProjectionManager = RuntimeProjectionManager`)
takes effect **only because `secure_live` is imported before the ASGI lifespan
runs**.

Consequences:

- `uvicorn auto_router.secure_live:app` (Dockerfile `CMD`, all compose files) ⇒
  **v2 / Ed25519**. Correct.
- `uvicorn auto_router.main_live:app` ⇒ **v1 / HMAC**. A weaker trust path is
  reachable from the module that the docs still describe as the runtime.
- **`pyproject.toml` ships `auto-router = "auto_router.main_live:run"` as a console
  script.** Installing the package and running `auto-router` therefore starts the
  **v1 HMAC** entrypoint, not the Ed25519 one. With no HMAC secret set, v1 rejects
  every projection (`validate_projection_document` raises on empty secret), so the
  outcome is fail-closed; but if an HMAC secret is present in the environment, a
  schema-v1 HMAC projection is accepted where the production entrypoint would
  demand schema-v2 Ed25519.
- `Makefile`'s `dev` target also runs `main_live:app`.

Output schemas **do** differ: v1 status reports `checksum`/`signature` on a
schema-1 document; v2 adds `signature_algorithm`, `signature_key_id`, a
base64url signature, and a `key_id` allow-list check, then re-signs an internal
schema-1 payload with `_INTERNAL_COMPAT_SECRET` so the v1 validator and swap
mechanics can be reused unchanged. v2 is therefore a strict trust upgrade layered
on v1's mechanics — **v1 is not dead code and must not be removed.**

**Recommendation (not applied — see §9 STOP list):** make the console script and
`Makefile dev` target point at `secure_live`, and make `main_live` refuse to start
without an explicit "I am running the compatibility HMAC manager" acknowledgement.
Both change deployment behaviour and are left to the operator.

---

## 4. Service-discovery truth: dataflow

```
discovered endpoint            service_scanner.discover_tailnet_lmstudio_services()
   (tailscale status --json)   → ContextService(service_id="tailnet.<slug>.lmstudio",
                               priority=90, tags={tailnet,discovered,...})
      │
      │  (also) ContextService rows projected from AssistX / config/context.yaml
      ▼
observed runtime               service_scanner.probe_service/_probe_http/_probe_tcp
                               → ServiceProbeResult(status=online|degraded|offline|unknown|blocked)
                               → service_store.ServiceStatusStore (SQLite, service_scan_events)
                               → state.service_status: ServiceStatusCache
                               → service_routes.apply_service_results_to_context (UNMOUNTED)
      │
      ▼
model residency                live_model_routes.fetch_provider_models (GET {base_url}/models)
                               → live_models.LiveModelCache (TTL, in-memory)
                               → model_registry.ModelRegistryStore.save_snapshot/save_probe (SQLite)
                               → live_model_routes.sync_live_models_to_providers()
                                  (writes state.providers[].models — the routing source of truth)
      │
      ▼
projection                     secure_live → runtime_projection_v2.RuntimeProjectionManager.apply()
                               → ProviderRegistry / AdmissionController / AccessPathSelector
                               → replaces state.providers, state.context, state.policy_engine,
                                 state.admission, state.access_paths atomically
      │
      ▼
registry                       ContextSnapshot (nodes/providers/models/services/signals)
                               + ContextSignalStore (signal_registry)
      │
      ▼
route eligibility              PolicyEngine (config/policies.yaml + context)
                               → strict_assistx_routes guard
                               → main_live._admitted_dispatch
                                   _projection_manager().assert_current_fresh()   ← fail-closed
                                   admission.acquire()                             ← capacity
                                   claim_fence.assert_executor_claim_current()     ← AssistX authority
                                   access_paths.select()                           ← approved URL
                                   main_module._dispatch
      │
      ▼
selected                       route_events / event_outbox → AssistX
```

### 4.1 The seven states, and which are preserved

| state | owner | meaning | changed by this campaign |
|---|---|---|---|
| `registered` | `ProviderRegistry` / `ContextSnapshot.providers` | present in configuration or projection | no |
| `reachable` | `service_scanner.probe_service`, `access_paths` probe | an HTTP/TCP connect succeeded | no |
| `healthy` | `access_paths` TTL cache, `circuit_breaker` | recently succeeded and not tripped | no |
| `model-loaded` | `live_model_routes._is_model_loaded` (`loaded_instances`) | resident on the endpoint, per LM Studio | no |
| `admitted` | `admission.RuntimeAdmissionController` | a lease was granted against `parallel_slots` | no |
| `routable` | `policy.PolicyEngine` + `strict_assistx_routes` | matches an enabled policy stage | no |
| `selected` | `route_events` → AssistX outbox | a concrete provider/model was chosen | no |

### 4.2 Stale or duplicated transitions

- **S1 — `ContextSnapshot` private-attribute caches are never invalidated.**
  `_all_models_cache` / `_model_index_cache` / `_signal_index_cache` are populated
  on first `all_models()` / `model_for()` / `signals_for()` call. `_project_live_models`
  (`config.py:506`) calls `snapshot.model_for(...)` **before** replacing
  `snapshot.models` and `provider.models` in place. The stale caches then propagate
  through `signal_registry.hydrate_context`, which uses `snapshot.model_copy(...)` —
  and pydantic copies `__pydantic_private__` **by value**, so the hydrated snapshot
  carries the stale index forward.
  *Reproduction*: `tests/test_config.py::test_project_live_models_merges_registry_snapshots_into_context`
  is **deselected in the blocking suite** because of this, and the four
  `tests/test_live_models.py` / `tests/test_live_model_routes.py` cases fail for the
  same reason. **Five tests in total encode the current behaviour.**

  *Measured blast radius* (`tests/test_stale_context_cache_repro.py`, added in slice 3
  specifically to settle this rather than assert it):

  | surface | affected? | why |
  |---|---|---|
  | `all_models()` / `local_models()` / `free_api_models()` / `model_for()` | **yes** | memoised before the mutation |
  | `ops_dashboard_routes` model counts and lists | **yes** | sole consumer of the above |
  | model selection | **no** | `PolicyEngine` reads `state.providers[].models`; `config._project_live_models` never writes to the provider registry (that is `live_model_routes.sync_live_models_to_providers`, a different function) |
  | signal-based blocking (`_provider_signal_blocked`) | **no** | `canonical_model_id` falls back to `str(model_id).strip().lower()` on an index miss, and `ContextModel.model_id` is always lowercase (`config._provider_scoped_model_id`), so the fallback reproduces the canonical id and the signal is found |
  | `_signal_index_cache` | **no** | `save_snapshot()` iterates `snapshot.signals` directly and never populates it, so it is still `None` across the `model_copy` and is rebuilt from the hydrated list |
  | `graph_objects()` / `all_services()` | **no** | iterate the fields directly, not the caches |

  **Correction to the first pass of this audit**: I initially suspected this opened a
  fail-open path, because `policy._context_signals` does route through
  `canonical_model_id` → `model_for` → the stale index. It does not — the lowercase
  fallback absorbs it, and the blocking-penalty assertion holds
  (`test_model_scoped_blocking_signal_still_resolves`). The defect is **operator
  visibility only**: the dashboard under-reports live-discovered models.
  **Not patched** — see §9 and §12.
- **S2 — two independent "is this model usable" truths.**
  `live_model_routes.sync_live_models_to_providers` is authoritative for the
  provider registry; `config._project_live_models` builds a parallel context view.
  They are not reconciled, so `/v1/models` (registry) and the dashboard/context
  (context view) can disagree for one poll interval or indefinitely after S1.
- **S3 — `service_store` / `service_scanner` are unmounted in production.**
  `register_service_routes` has **zero callers** (see §6). The SQLite
  `service_scan_events` table is therefore only written by
  `tests/test_service_routes.py`. Status shown in the ops dashboard comes from
  `context.services[].status`, which in the strict-offline deployment is whatever
  AssistX projected — never locally probed.
- **S4 — `merge_discovered_lmstudio_providers` mutates the routing registry from
  observation.** `live_model_routes.py:309` appends **new enabled providers** built
  from discovered services (`_provider_from_service`, `enabled=True`, `quota_class="local"`,
  **no models**) and can flip an existing `enabled: false` provider to
  `enabled: true` (`"enabled": bool(current.enabled or provider.enabled)`), overwrite
  its `base_url`, and lower its `priority` via `min(...)`.
  `config/providers.yaml` contains `lmstudio-joyner` with `enabled: false`, and
  `discover_tailnet_lmstudio_services()` derives provider names of the form
  `lmstudio-<slug>` from `tailscale status` on port 1234. **A drained node can
  therefore be re-enabled by discovery.** In the production lifespan
  (`strict_offline_lifespan`) this runs **exactly once**, from `load_state()`, before
  `state.context` exists — so at boot only tailnet discovery contributes.
  `refresh_tailnet_provider_task` and `refresh_live_models_task`, which would repeat
  it every 120 s / 5 s, are defined in `main.lifespan` but **not started** by
  `strict_offline_lifespan`. **Not patched** — see §9.
- **S5 — one-off / transitional loadout tooling.** `scripts/build_fleet_loadouts.py`
  writes Neo4j directly and is excluded from the reconciliation runtime by design
  (`pyproject` `legacy-loadout` extra, `Makefile test-legacy-audit`, and the CI
  workflow `fleet-loadout-neo4j.yml`).

---

## 5. Archived / static-fallback reachability

**Question: can any active code load `archive/`?**

**Answer: no.** Evidence:

1. `git grep -In "archive" HEAD -- .` filtered to exclude `HEAD:archive/` returns
   **0 lines**. No loader, compose file, Makefile target, script, test, or document
   at the base commit names the directory.
2. `git grep -In "static-fallback|static_fallback|providers\.yaml\.bak" HEAD -- .`
   filtered to exclude `HEAD:archive/` returns **0 lines**.

   (After this campaign those two greps *do* match — but only inside
   `archive/README.md` and this document, both of which are documentation about the
   absence of references. Re-run against `HEAD` to reproduce the baseline.)
3. No code performs filesystem glob discovery over `config/` or the repository
   root, so no `*.bak` / `*.static-fallback` file can be picked up implicitly.
   `grep -rn "\bglob\b|\.glob\(|iglob|rglob" --include="*.py" src/ tests/ scripts/`
   returns nothing.
4. `tests/test_housekeeping_config_authority.py::test_canonical_config_loading_never_opens_archived_artifacts`
   instruments `pathlib.Path.open` while all four canonical loaders run and asserts
   no archived path is opened. Observed set:
   `config/providers.yaml`, `config/policies.yaml`, `config/agent_workers.yaml`,
   `config/context.yaml`.

**What actually happens on missing canonical configuration:**

| missing | behaviour | verdict |
|---|---|---|
| `AUTO_ROUTER_PROVIDER_CONFIG` | `enforce_strict_offline_provider_config()` raises `RuntimeError` at import of `main_live`/`secure_live` (`validate_offline_provider_config` returns `["provider config does not exist: …"]`) | **fail clearly** |
| `AUTO_ROUTER_CONTEXT_CONFIG` (local path) | `ContextSnapshot(revision="bootstrap")` built from the provider/agent registries, labelled `projection_status: "bootstrap"`, `projection_loaded: false`, `projection_source: <path>`, `projection_error: ""` | **fail clearly (labelled), not fail-open** |
| `AUTO_ROUTER_CONTEXT_CONFIG` (http) unreachable | same bootstrap snapshot but labelled `projection_status: "bootstrap_fallback"`, `projection_reachable: false`, `projection_error: <exception>`; `ContextSnapshot.is_projection_degraded()` returns `True` and `preflight` reports `critical` | **fail clearly** |
| `AUTO_ROUTER_POLICY_CONFIG` / `_AGENT_CONFIG` | `WARNING: Config files not found` printed; empty registries; `preflight` warns/fails | weak but not authority-widening |
| `archive/providers.yaml.bak` pointed at directly | rejected by the offline guard: `groq: quota_class must be 'local', got 'fast_free'`, `groq: public or unresolved provider host 'api.groq.com' is forbidden`, plus cerebras/openrouter equivalents | **fail clearly** |

So the desired contract — *missing canonical configuration → fail clearly* — already
holds for the production entrypoints. **No fail-closed patch was required**, and the
archived static fleet state cannot be silently reactivated. Recorded in
`archive/README.md`.

One latent hazard is documented rather than closed: `archive/providers.yaml.bak`
would only be rejected because `main_live`/`secure_live` run the offline guard.
Running `uvicorn auto_router.main:app` directly bypasses `enforce_strict_offline_provider_config`
entirely and will load whatever `AUTO_ROUTER_PROVIDER_CONFIG` names. See §9 **X2**.

---

## 6. Compose / deployment drift

### 6.1 Profile map

| file | layer | entrypoint (`command`) | published ports | networks | volumes | purpose |
|---|---|---|---|---|---|---|
| `docker-compose.yml` | base | *none* ⇒ Dockerfile `CMD` = `auto_router.secure_live:app` | `${AUTO_ROUTER_BIND:-0.0.0.0}:${AUTO_ROUTER_HOST_PORT:-8088}:8088` | `default` + external `assistx` (alias `router`) | `./config:ro`, `./data`, `./artifacts`, 2× host PEM → `/run/secrets` | canonical fleet router |
| `docker-compose.integration.yml` | overlay (needs Compose ≥ 2.24.4 for `!override`) | `uvicorn auto_router.secure_live:app` | `127.0.0.1:8088:8088` | `default` only | `./config:ro`, `./data-integration` | CI smoke |
| `compose.reconciliation.yml` | overlay | `uvicorn auto_router.secure_live:app` | `127.0.0.1:${RECONCILIATION_ROUTER_PORT:-18088}:8088` | `auto_router_reconciliation_default` + external `assistx` | `./config:ro`, `./data-reconciliation`, `./artifacts-reconciliation`, executor + projection PEM | side-by-side shadow router |
| `docker-compose.agentgateway.yml` | overlay | n/a (adds `agentgateway`, `jaeger`, `otel-collector`) | `127.0.0.1:3000/15020/16686/14268/4317/4318` | default | `./config/agentgateway.local.example.yaml`, `./otel-collector-config.yaml` | **SUPERSEDED** |
| `examples/docker-compose.tailscale-sidecar.yml` | standalone example | *none* ⇒ Dockerfile `CMD` | none for the app (`network_mode: service:auto-router-ts`) | `router` + external `assistx` | `auto-router-tailscale-state`, `../tailscale/auto-router`, `../config:ro`, `../data`, `../artifacts` | tailnet-only reference topology |

Intentional differences (documented, **not** consolidated):

- The shadow router and the smoke router deliberately publish to `127.0.0.1` on
  non-conflicting ports (`18088`, `8088`) so they can run beside the production
  container.
- The reconciliation overlay renames **both** the default network
  (`auto_router_reconciliation_default`) and the external assistx network
  (`${RECONCILIATION_ASSISTX_NETWORK:-assistx_reconciliation_shared}`) so the shadow
  router never joins the production AssistX network by accident.
- `examples/docker-compose.tailscale-sidecar.yml` is intentionally *not* an overlay.
  Its own header states that Compose merge semantics around `network_mode` +
  `networks`/`ports` produce an invalid or unexpectedly exposed service.
- Source-tree read-only mounts (`./config:/app/config:ro`) are intentional: the
  provider/policy/worker registries are operator-editable by design. Writable
  mounts are limited to the per-profile data directories.

### 6.2 Defects

- **C1 — FIXED (slice 2) — CI `integration-smoke` could not start.**
  `docker-compose.yml` declares `env_file: [".env"]` and `.env` is gitignored.
  Compose treats a missing env file as a hard error, verified:
  `env file …/.env not found: … No such file or directory`. A fresh CI checkout had
  no `.env` and no step created one, so
  `docker compose -f docker-compose.yml -f docker-compose.integration.yml up -d --build`
  failed before building. Second, independent blocker: base-file `${VAR:?}`
  interpolation is evaluated while parsing the *base* file even when the overlay
  overrides the variable, so `AUTO_ROUTER_INTERNAL_SERVICE_TOKEN` had to exist in
  the environment regardless of the overlay's `smoke-service-token`. Reproduced
  locally with `docker compose config`.
  **Fix**: `.github/workflows/ci.yml` gained a step writing a one-line `.env`
  containing only `AUTO_ROUTER_INTERNAL_SERVICE_TOKEN=ci-parse-time-placeholder`.
  Deliberately **not** a copy of `.env.example`: that file also defines
  `AUTO_ROUTER_CONTEXT_CONFIG` and `AUTO_ROUTER_RUNTIME_PROJECTION_URL`, which would
  change the *rendered* values the overlay expects to inherit from the base
  defaults (see C4). Verified with `docker compose config` — the render is
  unchanged apart from the interpolation placeholder, and `AUTO_ROUTER_ADMIN_TOKEN`,
  `AUTO_ROUTER_INTERNAL_SERVICE_TOKEN`, `AUTO_ROUTER_CONTEXT_CONFIG` and
  `AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED` all resolve to the smoke values CI asserts
  against. **Not run end-to-end** (no Docker stack available here); the render is
  the mechanical guarantee.
- **C2 — smoke overlay inherits a secret path whose volume mount is overridden away.**
  `AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE=/run/secrets/assistx_executor_public_key.pem`
  is inherited from the base file, but `docker-compose.integration.yml` replaces
  `volumes:` with `!override`, so the PEM is never mounted. The file is therefore
  absent in the smoke container. Every signed executor token is then rejected and
  reported as `executor token signature is invalid`, which misdirects diagnosis
  toward the caller instead of the deployment; see **F2** in §9.
- **C3 — smoke overlay inherits `AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL=http://assistx-api:8000`.**
  Nothing serves `assistx-api` in the smoke network. It is only reached from
  `claim_fence` for executor-token requests, which the smoke test does not issue.
- **C4 — smoke overlay inherits `AUTO_ROUTER_RUNTIME_PROJECTION_URL=http://host.docker.internal:8000/api/router/runtime-projection`.**
  Nothing serves it in CI. `projection_poll_task` records a `last_error` and keeps
  retrying every 5 s. The CI assertions (`configured is False`, `current is None`)
  still hold, so the test passes, but the smoke run is not exercising the same
  posture as production. Recommend `""` in the overlay.
- **C5 — default credentials via `:-` interpolation.**
  `docker-compose.yml:28` `AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS: ${…:-change-me}` and
  `compose.reconciliation.yml:35` `${…:-change-me-shadow-api}`. These are
  inconsistent with the neighbouring `${VAR:?set …}` tokens on the same blocks.
  `.env.example` also ships `change-me` placeholders. **Not patched** — see §9.
- **C6 — `docker-compose.agentgateway.yml` injects hosted provider keys**
  (`GROQ_API_KEY`, `CEREBRAS_API_KEY`, …) and mounts an *example* gateway config as
  live configuration. It is forbidden on the current deployment (banner added by
  this campaign; `Makefile gateway-*` already exit 2).
- **C7 — documentation-only containers.** `jaeger` and `otel-collector` are declared
  in the agentgateway overlay only. `AUTO_ROUTER_OTEL_ENABLED` is `false` in every
  canonical compose file, and `otel.init_otel` is a no-op when disabled. No code
  depends on either container's hostname.
- **C8 — no code path depends on a Compose-only hostname.** `assistx-api`,
  `assistx-canary-api`, and `assistx-canary-api:8000` appear **only** as environment
  values, never in Python. The only Compose-only hostname with a code default is
  `gateway_config`'s `http://agentgateway:3000` / `http://agentgateway:3000/v1`,
  reachable only when `AUTO_ROUTER_AGENTGATEWAY_ENABLED=true` — `false` everywhere
  canonical. `host.docker.internal` / `gateway.docker.internal` are explicitly
  allow-listed in `offline_guard.host_is_offline_allowed` and `access_paths`.
- **C9 — console-script entrypoint drift.** `pyproject.toml` ships
  `auto-router = "auto_router.main_live:run"`, i.e. the v1-HMAC entrypoint, while
  every container runs `secure_live`. See §3.2.

---

## 7. Documentation classification

Full index with per-document rationale: [`docs/README.md`](../README.md). Each
document below carries an inline `> **Status: …**` banner; historical content is
byte-unchanged.

| classification | documents |
|---|---|
| **CURRENT** | `HIGH_LEVEL_DESIGN.md`, `LOW_LEVEL_DESIGN.md`, `fleet_outage_recovery.md`, `TAILSCALE_DOCKER.md`, `AGENT_INFRA_VISIBILITY.md`, `HETEROGENEOUS_FLEET_ROUTING.md`, `HETEROGENEOUS_FLEET_ROUTING_CHECKLIST.md`, `fleet-loadouts.md`, `FLEET_EXPERIENCE_MEMORY.md`, `AGENT_SKILLS.md` |
| **CURRENT_BUT_INCOMPLETE** | `DEPLOYMENT.md`, `ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md`, `DASHBOARD.md`, `SECURITY_PRIVACY.md` |
| **HISTORICAL** | `HLD.md`, `LLD.md`, `PRODUCTION_DEPLOYMENT.md`, `OPERATOR_RUNBOOK.md`, `SERVICE_DISCOVERY.md`, `FLEET_ROUTING_POLICY.md`, `AGENTGATEWAY_INTEGRATION.md`, `CEREBRAS_FLASH_NODE.md`, `PROVIDER_MATRIX.md`, `QUOTA_STRATEGY.md`, `ROUTER_SWITCHING_MECHANISM.md`, `ALIGNMENT_EVENT.md`, `NEO4J_ASSISTX_INTEGRATION.md`, `TODO.md` |
| **SUPERSEDED** | (same set as HISTORICAL where a successor exists: `HLD.md`→`HIGH_LEVEL_DESIGN.md`, `LLD.md`→`LOW_LEVEL_DESIGN.md`, `PRODUCTION_DEPLOYMENT.md`/`OPERATOR_RUNBOOK.md`→`DEPLOYMENT.md`, `SERVICE_DISCOVERY.md`/`FLEET_ROUTING_POLICY.md`→`HIGH_LEVEL_DESIGN.md`) |
| **PLAN_ONLY** | `20260609-next-version-plan.md`, `IMPLEMENTATION_PLAN.md`, `IDEAS.md`, `MEANINGFUL_OUTCOMES_ROADMAP.md`, `SWARM_UNIFICATION_BACKLOG.md`, `SWARM_UNIFICATION_CHECKLIST.md`, `SWARM_UNIFICATION_EXECUTION_PLAN.md`, `plans/*` (3 files) |
| **UNKNOWN** | none |

Docs-vs-code drift found:

- **D1 — `PRODUCTION_DEPLOYMENT.md:104` recommends `AUTO_ROUTER_CONTEXT_CONFIG=config/context.yaml`.**
  Every canonical compose file sets an `http(s)` AssistX projection instead, so the
  documented local-file configuration is not the deployed posture.
- **D2 — FIXED (slice 2) — `preflight._check_policy_registry` required retired
  profile names.** The check asked for `{backlog_burn, flash_start_planner,
  sophia_realtime}`; canonical `config/policies.yaml` ships `backlog_local` and
  `local_planner` (the hosted flash-start lane was removed). Only `sophia_realtime`
  matched, so `/admin/ops/preflight` reported a permanent "recommended policy
  profiles are missing" warning against the *correct* configuration. The stale names
  live in `config/policies.example.yaml`, which is where the check's expectations
  came from. The check now requires the canonical names, and
  `test_policy_check_passes_against_the_canonical_registry` reads the real
  `config/policies.yaml` so the drift cannot recur. Read-only operator report;
  `report["status"]` is unaffected because `warn` does not demote readiness.
- **D3 — `docs/LLD.md:105-107` route table** lists `/admin/context`,
  `/admin/circuits` and neighbours as operator endpoints; the security posture for
  them changed (see §9 X1).
- **D4 — `config/providers.yaml` carries `max_concurrency` and `recommended_use` on
  six models.** Neither is a field on `ModelConfig`, and pydantic `extra="ignore"`
  drops both. An operator editing those keys gets no effect and no warning.
- **D5 — `config/providers.yaml` `lmstudio-destroyer-k2` uses a literal
  `base_url: http://destroyer.tailcb8954.ts.net:1235/v1`** while every sibling uses
  `${VAR:-default}`. Intentional (it is the newest commit, `034d108`) but
  inconsistent, and it is not operator-overridable without editing the file.

---

## 8. Dead and duplicate code

### 8.1 Removed — none

**No code was deleted in this campaign.** Every candidate below failed the
evidence bar for removal: each is either intentionally unmounted-but-retained
history with an explicit CI assertion, or shares a module with live code. See
"recommended next slice".

### 8.2 Dead-code candidates with evidence — RETAINED

| candidate | evidence | disposition |
|---|---|---|
| `service_routes.register_service_routes` | **zero callers** repo-wide (no source, no test). CI `integration-smoke` asserts `/admin/services/scan` is **forbidden** in the mounted app | retain — unmounted by contract |
| `live_model_routes.register_live_model_routes` | zero callers. CI asserts `/admin/live-models` forbidden | retain — but note `merge_discovered_lmstudio_providers` / `refresh_provider_models` in the same module **are** live |
| `cli_routes.register_cli_routes` | zero callers. CI asserts `/admin/agent-clis/discover` forbidden | retain |
| `backlog_routes.register_backlog_routes` | only caller is `tests/test_backlog_routes.py`. CI asserts `/admin/backlog/*` forbidden | retain |
| `runtime_projection.py` (v1) | base class of v2; swap mechanics; `projection_poll_task` re-exported by v2 | retain — see §3 |
| `service_store.py` / `service_scanner.probe_service` writers | only reachable through unmounted routes; DB table written by tests only | retain |
| `scripts/build_fleet_loadouts.py` (direct Neo4j) | `pyproject` `legacy-loadout` extra documents it as "not used by the reconciliation runtime"; separate CI workflow | retain |
| `ModelConfig.allow_agent_runtime`, `ProviderConfig.allow_agent_runtime` | defined on both models; **never read** by any code or test | retain — report as dead schema fields |
| `ProviderConfig.local_gateway_only` | read only to add a context *tag* (`config.py:575`); never consulted for gateway routing | retain — name is misleading |
| `config/providers.yaml` `max_concurrency`, `recommended_use` | not `ModelConfig` fields; silently dropped | retain — operator-visible, report as drift (**D4**) |

### 8.3 Confirmed duplication

| duplication | sites | disposition |
|---|---|---|
| `${VAR:-default}` expansion | `config._expand_env`, `offline_guard._expand_env`, `fleet_task_dispatcher._expand_env` (divergent regexes) | **RESOLVED (slice 2)** → `auto_router.env_expansion.expand_env` |
| SQLite URL → path | `ledger`, `service_store`, `memory_store`, `signal_registry`, `model_registry`, `event_outbox` (**6**, not 5) | **RESOLVED (slice 2)** → `auto_router.sqlite_path.sqlite_path` |
| `_merge_signals` | `config.py:632`, `signal_registry.py:644` (byte-identical bodies) | retained — merging feeds policy scoring; consolidation is low value but non-zero risk |
| Signal *construction* helpers | `config._graph_signal_context`, `signal_registry` row decoding, `live_model_routes` signal emission | retained — distinct inputs, shared identity semantics |

### 8.4 Unused templates / static fragments

`src/auto_router/templates/` is referenced by `ops_dashboard_routes` /
`ui_pages` / `main` and is live. `examples/docker-compose.tailscale-sidecar.yml`
references `../tailscale/auto-router` (a `serve.json` export directory) that is not
in the repository — documented in the file's own header as an operator-supplied
mount. No unused template fragments were found.

---

## 9. Security / fail-closed scan

### 9.1 Changes applied (high-confidence, authority-preserving)

**F1 — `docs/README.md`, inline doc banners, `docker-compose.agentgateway.yml`
banner, `archive/README.md`.** Operator-facing clarity only; no code or deployment
behaviour changed.

**F2 — `executor_auth._read_key` now converts an unreadable/absent key file into
`ExecutorAuthError`.**

```
- with open(path, "rb") as handle:
-     return handle.read()
+ try:
+     with open(path, "rb") as handle:
+         return handle.read()
+ except OSError as exc:
+     raise ExecutorAuthError(
+         f"auto-router executor verification key file is unreadable: {path}"
+     ) from exc
```

Why: `_load_public_key` wraps *every* other failure mode — absent key, PEM parse
failure, non-Ed25519 key — in `ExecutorAuthError`, but the file-read path was
missed and let a bare `OSError` through.

Scope correction (verified, not assumed): the `OSError` did **not** previously
escape to a 500. `decode_executor_token` wraps its `verify(...)` call in a broad
`except Exception` and re-raises `ExecutorAuthError("executor token signature is
invalid")` (`executor_auth.py:116`). So the request was already refused with HTTP
401 and never reached the downstream app — confirmed by
`test_unreadable_verify_key_file_rejects_rather_than_raising`.

What the patch actually buys is **diagnosis, not status**: the chained cause is now
an `ExecutorAuthError` instead of a `FileNotFoundError`, so a missing or unreadable
key file is distinguishable from a genuinely invalid caller token
(`test_unreadable_verify_key_file_is_distinguishable_from_a_bad_token`) while the
caller-visible message stays generic on purpose. This is directly reachable via
**C2**. It does not touch routing, admission, dispatch, model eligibility, or any
credential.

**F3 — `preflight._check_dry_run_posture` reads `agents.agent_workers`, not
`agents.workers`.**

`AgentWorkerRegistry` declares the field `agent_workers`. Reading `workers` always
returned `[]`, so the check reported *"dry-run posture preserved"* for **every**
deployment. With the fix, `/admin/ops/preflight` will correctly report
`warn: Some agent workers are enabled…` against the canonical
`config/agent_workers.yaml` (which enables `hermes`, `hermes-mini`,
`hermes-draft`, …). **Visible, intentional status change on a read-only operator
report**; `report["status"]` remains `ready` because only `fail` demotes it. The
`tests/test_preflight.py` fixture mirrored the same typo (`SimpleNamespace(workers=[])`)
and was corrected.

**F4 — new focused tests.** Slice 1: `tests/test_housekeeping_config_authority.py`
(10) plus 3 in `tests/test_executor_auth.py`. Slice 2: `tests/test_env_expansion.py`
(27) and `tests/test_sqlite_path.py` (42), plus 3 in `tests/test_preflight.py`.
Every new test was confirmed to **fail** when its fix is reverted (see §10.3).

**F5 — `auto_router.env_expansion.expand_env` replaces three divergent
`${VAR:-default}` expanders.** See **L5**. Unifying on the stricter
shell-identifier pattern can only narrow substitution, never widen it, and no
committed configuration uses a non-identifier name.
`config._expand_env` and `fleet_task_dispatcher._expand_env` are kept as aliases of
the shared function so every existing call site — including
`discovery_enabled_policy`, which reaches the dispatcher copy through the
`discovery` alias — is unchanged.

**F6 — `auto_router.sqlite_path.sqlite_path` replaces six identical
`_path_from_url` methods.** See **L6**. `tests/test_sqlite_path.py` carries the
original algorithm verbatim as a reference and asserts equality for every URL form.

**F7 — `preflight._check_policy_registry` requires the canonical profile names.**
See **D2**. Read-only operator report; `warn` does not demote `report["status"]`.

**F8 — CI `integration-smoke` can start again.** See **C1**. Workflow-only change;
no compose file, image, or runtime configuration was modified. Pins §2.2.
These are new assertions only; no existing assertion was weakened.

### 9.2 Findings NOT patched — deliberately stopped

Each of these is on the STOP list (routing/admission/dispatch/model eligibility,
model load-unload, or production network exposure).

**X1 — CRITICAL: 20 mounted endpoints have no admin guard.**
`auto_router.security.require_admin` gates only `/admin/quota`, `/admin/memory`,
`/admin/admission`, and `/admin/runtime-projection`. Every other `/admin/*`
endpoint defined in `main.py` is inherited by `main_live` (which removes only
`/jobs/agent`) and therefore **mounted in production**, unauthenticated. Verified by
introspecting the live ASGI app built from `secure_live`:

```
/health                              GET     NO
/metrics                             GET     NO
/dashboard                           GET     NO
/admin/quota                         GET     yes
/admin/providers/health              GET     NO
/admin/context                       GET     NO
/admin/context/graph                 GET     NO
/admin/providers                     GET     NO
/admin/placement                     GET     NO
/admin/load                          POST    NO      <-- see X2
/admin/agent-workers                 GET     NO
/admin/usage                         GET     NO
/admin/circuits                      GET     NO
/admin/circuits/reset                POST    NO
/admin/circuits/reset/{owner:path}   POST    NO
/admin/agent-jobs                    GET     NO
/v1/models                           GET     NO
/jobs/agent/{job_id}                 GET     NO
/jobs/agent/{job_id}/artifacts       GET     NO
/admin/ops/summary                   GET     NO
/admin/ops/preflight                 GET     NO
```

This directly contradicts three in-repo statements of intent:
`security.py` ("gates the sensitive `/admin/*` and `/jobs/agent` endpoints"),
`settings.py` ("the `/admin/*` and `/jobs/agent` endpoints require this shared
token"), and `LLD §3.5 W-64`. Information disclosure covers the entire fleet
topology (Tailscale hostnames, `100.x` CGNAT addresses, node IDs, base URLs,
runtime instance IDs) plus job artifacts and usage history.
**Not patched** — closing it changes production network exposure and could break
unknown automation that reads `/admin/context` (which `templates/base.html`,
`ui_pages.py`, and several docs link from the dashboard UI). Requires an operator
decision on which read-only endpoints stay open.

**X2 — CRITICAL: `POST /admin/load?apply=true` bypasses both autoload gates, unauthenticated.**
`model_placement.reconcile_once(state, apply=...)` treats an explicit `apply` as
authoritative: `do_apply = apply if apply is not None else AUTOLOAD_ENABLED`
(`model_placement.py:409`). `main.admin_load` passes the query parameter straight
through, so `?apply=true` reaches the load/unload loops
(`model_placement.py:411-464`) with `swappable_only=False`, ignoring
`AUTO_ROUTER_AUTOLOAD_ENABLED=false` and `AUTO_ROUTER_PLACEMENT_UNLOAD=false`
(set in all three canonical compose files). `UNLOAD_ENABLED` only gates whether
unloads appear in `compute_plan` (`model_placement.py:304`), not whether an
already-planned unload executes. An unauthenticated caller can therefore load and
unload models on every fleet node.
**Not patched** — model load/unload and dispatch authority. Recommended: require
`require_admin` on `/admin/load` *and* refuse `apply=true` unless
`AUTOLOAD_AUTOMATION_ENABLED` is separately set, keeping the documented
`/admin/placement` (dry-run, `swappable_only=False`, `apply=None`) as the safe
manual surface.

**X3 — HIGH: tailnet-observed peers mutate the routing registry (S4).**
`merge_discovered_lmstudio_providers` can re-enable a deliberately disabled provider
and overwrite operator-configured `base_url`/`priority` from observation alone.
Bounded in production to a single boot-time call (see S4), and discovered providers
carry **no models**, so they cannot directly serve traffic — but they do enter
`ProviderRegistry.enabled()`, the admission controller, and `/v1/models`
enumeration. **Not patched** — admission/routing authority.

**X4 — MEDIUM: default credentials via `:-` compose interpolation (C5).**
`AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS` falls back to `change-me` /
`change-me-shadow-api`, inconsistent with the `:?` tokens on the same blocks.
That credential is the Basic-auth pair the router presents to
`AUTO_ROUTER_RUNTIME_PROJECTION_URL` (`runtime_projection.py:465-467`) and the
AssistX outbox sink (`service_routes.py:25`). **Not patched** — changing `:?` makes
compose refuse to start where it previously started weak.

**X5 — MEDIUM: `auto-router.main:app` bypasses the strict-offline guard.**
`enforce_strict_offline_provider_config()` runs only in `main_live`/`secure_live`.
Running `uvicorn auto_router.main:app` (or the `pyproject` console script, which
targets `main_live`) with `AUTO_ROUTER_STRICT_OFFLINE` unset still defaults to
`true` inside `host_is_offline_allowed`/`validate_offline_provider_config`, but the
**startup validation never runs**, so a hosted provider registry such as
`archive/providers.yaml.bak` would load. Default-offline membership checks still
apply at request time. **Not patched** — entrypoint/exposure change.

**X6 — LOW: no CORS middleware is installed.** Verified: `grep -rn
"CORSMiddleware|allow_origins|CORS" src/` returns nothing. There is no
wildcard-CORS defect; browser same-origin policy applies. The exposed surface is
direct HTTP, covered by X1.

**X7 — LOW: `AUTO_ROUTER_BIND` defaults to `0.0.0.0`** in `docker-compose.yml`, and
`Settings.host` defaults to `0.0.0.0`. Intentional for a homelab service; flagged
for operator awareness only. **Not patched** — production network exposure.

### 9.3 Scanned and clean

- No default API keys in tracked source. `archive/providers.yaml.bak` references
  `GROQ_API_KEY`/`CEREBRAS_API_KEY` by *env name only*, no literal secrets.
- `require_admin` fails **closed** when `AUTO_ROUTER_ADMIN_TOKEN` is unset (401 with
  "Admin token not configured"), and uses `hmac.compare_digest` for the constant
  comparison.
- `runtime_projection_v2.load_public_key` fails closed on missing key, wrong type,
  or empty key material, and enforces a `signature_key_id` allow-list.
- `admission`, `access_paths`, and `claim_fence` all raise rather than degrade on
  absent/expired projections or unavailable AssistX.
- `executor_auth` enforces an `allowed_model_aliases` scope, input/output token
  budgets, projection-generation match, and per-`jti` attempt caps.
- No debug/test configuration is reachable in production: `AUTO_ROUTER_LOG_PROMPTS`
  is `false` in all canonical compose files and `preflight` fails on it;
  `AUTO_ROUTER_OTEL_ENABLED` is `false`; `AUTO_ROUTER_AGENTGATEWAY_ENABLED` is
  `false`. `docker-compose.integration.yml`'s `smoke-admin-token` /
  `smoke-service-token` are confined to that overlay, which CI already treats as
  hostile (it asserts 401/409 without them).
- `strict_assistx_routes.install_strict_assistx_route_guard` rejects non-local lanes
  on the reconciliation surface.

---

## 10. Tests

Baseline captured at `034d108` **before** any change, using a clean worktree
(`/home/scott/git/auto-router-hk-20261002`) and an isolated virtualenv
(`/tmp/opencode/ar-venv`). No existing assertion was weakened anywhere.

### 10.1 Baseline (unmodified `034d108`)

`pytest -q --ignore=tests/integration` → **7 failed, 311 passed, 2 skipped**

| failing test | cause |
|---|---|
| `test_config.py::test_macbook_air_provider_registry_includes_live_tailnet_models` | **pre-existing drift**: `config/providers.yaml` switched this node to `qwen3.5-0.8b-mlx` in `e473e48` (2026-08-17); the assertion still expects `qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled` from `9493bd5` (2026-07-14) |
| `test_config.py::test_project_live_models_merges_registry_snapshots_into_context` | stale `ContextSnapshot` private-attr caches (**S1**) |
| `test_live_models.py::test_refresh_provider_models_probes_all_enabled_providers_and_projects_context` | same (**S1**) |
| `test_live_models.py::test_discovered_lmstudio_providers_include_context_services` | same (**S1**) |
| `test_live_models.py::test_refresh_provider_models_replaces_stale_bootstrap_model_on_changed_node` | same (**S1**) |
| — *(5 failures attributable to **S1**)* | stale `ContextSnapshot` private-attr caches |
| `test_live_model_routes.py::test_refresh_provider_models_survives_registry_write_failures` | same (**S1**) |
| `test_backlog_routes.py::test_backlog_burn_down_uses_real_dispatch_path` | unmounted backlog surface |

Blocking CI suite at baseline (`bash scripts/test_reconciliation.sh`) →
**1 failed, 293 passed, 2 skipped, 1 deselected**. The single failure is the macbook
config/test drift — i.e. **CI `main` was already red for this reason**. The script
explicitly ignores `test_live_models.py` / `test_live_model_routes.py` /
`test_agent_jobs.py` / `test_backlog_routes.py` and deselects
`test_config.py::test_project_live_models_merges_registry_snapshots_into_context`,
which is how the five **S1**-caused failures are kept out of the blocking gate.

`pytest -q` including `tests/integration/` → 3 further failures
(`test_fleet_smoke.py::test_end_to_end_fleet_smoke`,
`test_health.py::test_assign_health`,
`test_health.py::test_assistx_router_projection_surface`), all
`httpx.ConnectError` against live fleet/AssistX addresses. These require the running
homelab and are excluded from the blocking suite by design. **Not run for real and
not contacted.**

### 10.2 After this campaign

| suite | result |
|---|---|
| `tests/test_housekeeping_config_authority.py` (new) | **10 passed** |
| `tests/test_preflight.py` | **4 passed** |
| `tests/test_executor_auth.py` | **6 passed** (3 pre-existing + 3 new) |
| `pytest -q --ignore=tests/integration` (after slice 2) | **7 failed, 396 passed, 2 skipped** |
| `bash scripts/test_reconciliation.sh` (after slice 2) | **1 failed, 378 passed, 2 skipped, 1 deselected** |

Delta versus the `034d108` baseline: **+85 net passing** (311 → 396), **0 new
failures**. The same 7 pre-existing failures remain, of which only 1 is in the
blocking suite. Per-slice: slice 1 +13, slice 2 +72.

### 10.3 Regression sensitivity of every new test

Each new or amended test was re-run against the reverted fix to confirm it actually
fails. Results:

| test | fix reverted → |
|---|---|
| `test_unreadable_verify_key_file_is_distinguishable_from_a_bad_token` | **fails** |
| `test_verify_key_file_oserror_is_wrapped_as_executor_auth_error` | **fails** |
| `test_dry_run_posture_detects_enabled_agent_workers` | **fails** |
| `test_policy_check_passes_against_the_canonical_registry` | **fails** |
| `test_policy_check_still_detects_a_genuinely_missing_profile` | **fails** |
| `test_all_committed_tokens_use_identifier_safe_names` (with a `${bad-name}` token injected into `config/providers.reconciliation.yaml`) | **fails** |

`test_unreadable_verify_key_file_rejects_rather_than_raising` does **not**
discriminate on its own — it pins behaviour that was already correct (401, downstream
never reached) and is retained as a contract guard, not a regression detector. The remaining failure in
both runs is the pre-existing `test_macbook_air_provider_registry_includes_live_tailnet_models`
config/test drift.

That single test was **deliberately left red**. Resolving it requires choosing which
side is authoritative — the fleet's real macbook-air inventory or the assertion —
and that cannot be determined without contacting the live node. Editing the
assertion to match the file would be making a test green without evidence;
editing `config/providers.yaml` would change live model eligibility. Both are
out of bounds.

`ruff check src tests` is **non-blocking by existing CI policy** (pre-existing
backlog, `continue-on-error: true`). Verified net-zero: `ruff check --select
I001,F401,F811 src/auto_router/` reports **27 findings, identical to the `034d108`
baseline** — slice 1 and slice 2 each left the count unchanged, and the two
pre-existing `I001` findings in `config.py` and `fleet_task_dispatcher.py` were
deliberately left in place rather than swept up into an unrelated reformat. All
four new test files report clean. The blocking gate
(`ruff check --ignore I001,E501,UP012,UP018` over the reconciliation path list)
passes; `executor_auth.py` and `preflight.py` are both inside it. `mypy
src/auto_router` remains `continue-on-error: true` and is unchanged in policy.

---

## 11. Changed files

```
docs/README.md                                          rewritten as a classified index
docs/housekeeping/auto-router-config-and-dead-path-audit-2026-10-02.md   (new)
archive/README.md                                       (new)
tests/test_housekeeping_config_authority.py             (new, 10 tests)
src/auto_router/env_expansion.py                        (new, slice 2) shared ${VAR:-default} expander
src/auto_router/sqlite_path.py                          (new, slice 2) shared SQLite URL -> Path resolver
src/auto_router/config.py                               delegates to env_expansion
src/auto_router/offline_guard.py                        delegates to env_expansion
src/auto_router/fleet_task_dispatcher.py                delegates to env_expansion
src/auto_router/ledger.py                               delegates to sqlite_path
src/auto_router/service_store.py                        delegates to sqlite_path
src/auto_router/memory_store.py                         delegates to sqlite_path
src/auto_router/signal_registry.py                      delegates to sqlite_path
src/auto_router/model_registry.py                       delegates to sqlite_path
src/auto_router/event_outbox.py                         delegates to sqlite_path
src/auto_router/executor_auth.py                        _read_key OSError -> ExecutorAuthError
src/auto_router/preflight.py                            _check_dry_run_posture + policy profile names
tests/test_env_expansion.py                             (new, 27 tests, slice 2)
tests/test_sqlite_path.py                               (new, 42 tests, slice 2)
tests/test_executor_auth.py                              3 new tests for F2
tests/test_preflight.py                                 fixtures + 3 new tests
.github/workflows/ci.yml                                integration-smoke .env step for C1
docker-compose.agentgateway.yml                         SUPERSEDED banner (comments only)
docs/DEPLOYMENT.md                                      banner
docs/PRODUCTION_DEPLOYMENT.md                           banner
docs/OPERATOR_RUNBOOK.md                                banner
docs/SERVICE_DISCOVERY.md                               banner
docs/FLEET_ROUTING_POLICY.md                            banner
docs/ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md           banner
docs/AGENTGATEWAY_INTEGRATION.md                        banner
docs/HLD.md                                             banner
docs/LLD.md                                             banner
docs/CEREBRAS_FLASH_NODE.md                             banner
docs/ROUTER_SWITCHING_MECHANISM.md                      banner
docs/PROVIDER_MATRIX.md                                 banner
docs/QUOTA_STRATEGY.md                                  banner
docs/ALIGNMENT_EVENT.md                                 banner
docs/NEO4J_ASSISTX_INTEGRATION.md                       banner
docs/IMPLEMENTATION_PLAN.md                             banner
docs/IDEAS.md                                           banner
docs/TODO.md                                            banner
docs/20260609-next-version-plan.md                      banner
docs/SWARM_UNIFICATION_BACKLOG.md                       banner
docs/SWARM_UNIFICATION_CHECKLIST.md                     banner
docs/SWARM_UNIFICATION_EXECUTION_PLAN.md                banner
docs/DASHBOARD.md                                       banner
docs/SECURITY_PRIVACY.md                                banner
```

No changes to: `config/*.yaml` (any), `archive/context.yaml.static-fallback`,
`archive/context.yaml.static-fallback.note`, `archive/providers.yaml.bak`,
`docker-compose.yml`, `docker-compose.integration.yml`,
`compose.reconciliation.yml`, `examples/docker-compose.tailscale-sidecar.yml`,
`Dockerfile`, `pyproject.toml`, `Makefile`, `.env.example`, `scripts/*`, or any
routing / admission / policy / provider / dispatch module.

`.github/workflows/ci.yml` **is** changed, but only by adding a step that writes a
one-line CI-local `.env`. No workflow gate, assertion, image, or compose file was
altered, and no workflow behaviour outside `integration-smoke` was touched.

Slice 2 deliberately did **not** touch `config/providers.yaml`, even though
`max_concurrency` and `recommended_use` are proven-inert keys (**D4**). Removing
them is behaviour-neutral in pydantic terms, but the file is the live fleet
registry and that edit belongs to an operator, not to housekeeping.

---

## 12. Recommended next slice

Ordered by risk-reduction per unit of operator review. **Items 1–3 require an
explicit operator decision and are not safe for an unattended agent.**

1. **Close X1 (unauthenticated `/admin/*`).** Decide which read-only endpoints stay
   open (`/health`, `/metrics`?) and gate the rest with the existing
   `require_admin`. Update the dashboard templates, `ui_pages.py`, and the CI
   `integration-smoke` assertions in the same change so the UI and the smoke test do
   not silently break.
2. **Close X2 (`/admin/load` autoload bypass).** Require `require_admin` *and* a
   separate opt-in env for `apply=true`, leaving `/admin/placement` (dry-run) as the
   default manual surface.
3. **Fix C1 (CI `integration-smoke` cannot start).** Either add a CI step that writes
   a `.env` from `.env.example`, or make `env_file` optional
   (`required: false`, Compose ≥ 2.24) — this also unblocks C4 by letting the
   overlay set `AUTO_ROUTER_RUNTIME_PROJECTION_URL: ""`.
4. ~~**Fix C2/C3/C4 (smoke overlay inherits base secrets and hosts).**~~ Still open.
   C4 in particular is a judgement call: clearing
   `AUTO_ROUTER_RUNTIME_PROJECTION_URL` would stop the smoke run exercising
   `projection_poll_task`'s failure path, which may be worth keeping. Needs a
   deliberate decision, not a mechanical change.
5. **Resolve S1 (`ContextSnapshot` cache invalidation).** The correct fix is to
   invalidate the private-attr caches whenever `_project_live_models` (or any other
   in-place mutator) replaces `snapshot.models` / `provider.models` — e.g. a
   `ContextSnapshot.invalidate_caches()` called from the mutators, or making those
   fields a validated property. This **will change live-model visibility in the
   context view**, so it must be paired with the five currently-failing
   `test_live_models` / `test_live_model_routes` / `test_config` cases and a
   deliberate decision that the context view should follow the live projection.
6. **Reconcile the macbook-air config/test drift** (D5/pre-existing failure). Requires
   confirming the node's real resident inventory from the fleet, then updating
   exactly one side.
7. ~~**Consolidate the triplicated `${VAR:-default}` expander** … Then the SQLite
   `_path_from_url` copies.~~ **DONE in slice 2** (`env_expansion`, `sqlite_path`).
   Remaining in this family: `_merge_signals` (2 byte-identical copies) and
   `Settings.latency_cache_path` (deliberately excluded from `sqlite_path`).
8. **Decide the fate of the unmounted route registrars**
   (`register_service_routes`, `register_live_model_routes`, `register_cli_routes`,
   `register_backlog_routes`) — move them out of `src/` into a clearly-marked
   historical package, or delete them once the CI "forbidden paths" assertions are
   updated. Do not do this silently; the CI assertions are the contract that keeps
   them unmounted.
9. **D2 is FIXED in slice 2.** Still open: **D4** (drop or implement
   `max_concurrency` / `recommended_use`, three sites each in
   `config/providers.yaml`) and documentation for the dead `allow_agent_runtime`
   fields and the misleading `local_gateway_only` name. Both need an operator
   decision on the live fleet registry.

---

## 13. Remaining ambiguities

| # | ambiguity | why unresolved |
|---|---|---|
| A1 | Is `qwen3.5-0.8b-mlx` or `qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled` the correct macbook-air resident? | requires live fleet observation, which is out of scope |
| A2 | ~~Is S1 a routing defect?~~ **Resolved by measurement in slice 3: no.** It is operator visibility only. What remains open is whether to fix it at all — five existing tests encode the current expectation, and the fix makes them pass, so it is a deliberate behaviour change to the dashboard rather than a red-to-green cleanup |
| A3 | Is boot-time tailnet provider merging (**S4**/X3) a deliberate migration shim or a defect? | needs the intent behind `refresh_tailnet_provider_task` being excluded from `strict_offline_lifespan` |
| A4 | Which unauthenticated `/admin/*` reads (X1) have real consumers? | templates, `ui_pages.py`, and several docs link them; needs operator inventory |
| A5 | Is the `auto-router` console script intentionally the v1/HMAC entrypoint? | `pyproject.toml` and `Makefile dev` disagree with the Dockerfile; no commit message states intent |
| A6 | Should `config/context.yaml` and `config/context.example.yaml` (byte-identical) remain in sync with each other? | W-58 retired static context as truth, but the local-file form is still the `Settings` default |
| A7 | Are `ModelConfig.allow_agent_runtime` / `ProviderConfig.allow_agent_runtime` reserved for AssistX projection use, or abandoned? | never read, never projected, no documentation |

---

## 14. Impact of concurrent commit `d3a9258` on `main`

`d3a9258` (Remediation Bot, 2026-10-02 15:04) changes `docker-compose.yml` only.
It was not authored by this campaign and was left untouched. Three of its effects
bear directly on this audit.

### 14.1 X3 / S4 escalate from "latent" to "reachable in the production container"

`d3a9258` mounts the Tailscale daemon socket and CLI into the router container:

```yaml
- /var/run/tailscale/tailscaled.sock:/var/run/tailscale/tailscaled.sock:ro
- /usr/bin/tailscale:/usr/bin/tailscale:ro
```

That is exactly what `service_scanner.discover_tailnet_lmstudio_services()` needs in
order to stop returning `[]` early:

```
secure_live lifespan
  → main.load_state()                      (main.py:228 → main.py:80)
    → merge_discovered_lmstudio_providers(state)
      → discovered_lmstudio_providers()
        → discover_tailnet_lmstudio_services()   (live_model_routes.py:264)
          → subprocess.run(["tailscale", "status", "--json"])   ← now has a binary + socket
```

Before `d3a9258` this `subprocess.run` raised and the function returned `[]` in the
container, so the tailnet half of S4 was effectively dead in production. After it,
`tailscale status --json` can succeed, peers on port 1234 become
`ContextService` records, and `merge_discovered_lmstudio_providers` can therefore:

* append new `enabled=True` providers into the routing registry, and
* **flip an operator-disabled provider back to enabled** — `config/providers.yaml`
  ships `lmstudio-joyner` with `enabled: false`, and the merge computes
  `bool(current.enabled or provider.enabled)`.

It remains bounded to **one call at boot** (before `state.context` exists), and
`refresh_tailnet_provider_task` / `refresh_live_models_task` are still not started by
`strict_offline_lifespan`. Discovered providers also carry **no models**, so they
cannot directly serve traffic — but they do enter `ProviderRegistry.enabled()`, the
admission controller, and `/v1/models` enumeration.

**The single lever that disables this is `AUTO_ROUTER_DISCOVER_TAILNET_LMSTUDIO=0`**,
which `service_scanner._env_flag` already honours and which no canonical compose file
sets. **This is now the highest-value one-line hardening change available**, because
it removes the discovery input entirely without altering routing, admission, or
dispatch semantics for anything the operator configured deliberately.

### 14.2 C8 no longer holds as written

This audit previously recorded: *"No code path depends on a Compose-only hostname."*
That was true of **Python code** and remains true — `assistx-api` still appears only
as an environment value. But `d3a9258` makes the canonical compose file **default**
two URLs to a Compose-network-only name:

```
AUTO_ROUTER_CONTEXT_CONFIG          → http://assistx-api:8000/api/router/context-projection
AUTO_ROUTER_RUNTIME_PROJECTION_URL  → http://assistx-api:8000/api/router/runtime-projection
```

(both were `http://host.docker.internal:8000/...`). The router container joins the
external `assistx` network, so this resolves only if that external network actually
publishes an `assistx-api` service. `compose.reconciliation.yml` deliberately uses a
different name (`assistx-canary-api`) on a different network, so the two profiles now
disagree on the AssistX DNS name. Worth confirming which name the production AssistX
network publishes.

### 14.3 C4 changes character under the smoke overlay

`docker-compose.integration.yml` does not override
`AUTO_ROUTER_RUNTIME_PROJECTION_URL`, so it inherits the base default. Under
`034d108` that was `host.docker.internal:8000` — resolvable in CI via the inherited
`extra_hosts: ["host.docker.internal:host-gateway"]`, with nothing listening. Under
`d3a9258` it becomes `assistx-api:8000`, which does not resolve on the
`auto-router-smoke` network at all. The projection poll task fails either way and CI's
`configured is False` / `current is None` assertions still hold, so this is not a
regression — but the recommended fix in C4 is now *more* clearly correct.

This also retroactively validates a slice-2 decision: the CI `.env` fix writes a
one-line placeholder rather than copying `.env.example`, and `.env.example` sets
`AUTO_ROUTER_RUNTIME_PROJECTION_URL=http://assistx-api:8000/...`. Copying it would
have silently pinned the smoke profile to a live AssistX DNS name. Verified: the
slice-2 `.env` fix renders cleanly on top of `d3a9258`
(`docker compose config` exits 0 with the expected smoke values).

### 14.4 New cross-repository coupling not previously audited

`d3a9258` also mounts a file from **another repository** into the router container:

```yaml
- /media/.../home_scott_git_auto-assist/src/assistx/fleet_reservations.json:/run/assistx/fleet_reservations.json:ro
```

This campaign made no change to `auto-assist` and did not read that path. Two things
are worth recording for whoever reviews the deployment:

* No Python code in this repository references
  `/run/assistx/fleet_reservations.json` (verified: zero hits for
  `fleet_reservations` in `src/`). The mount is currently inert, or it is consumed by
  something outside the committed source — which would itself be a finding.
* It is an unbound host-path dependency on a sibling repo's working tree, in the same
  style as the two existing `assistx-runtime-projection-ed25519-public.pem` mounts,
  but pointing at source rather than a config secret.

### 14.5 What this campaign did **not** do about it

`d3a9258` was left exactly as committed. No revert, no amend, no rebase of this
branch onto it, and no edit to `docker-compose.yml` on `main`. Landing this branch is
an operator decision: it is two clean, self-contained commits on top of `034d108`,
and `d3a9258` is a single unrelated commit on top of the same parent.
