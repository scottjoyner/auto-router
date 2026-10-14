# Auto-Router integration / physical provenance release packet — 2026-10-14

> **Status:** UNRELEASED DRAFT / P0 NO-GO. **Scope:** strict-offline routing, admin ingress, signed task provenance and physical native execution. **Source commit:** current GitHub main 747530a9a79b8d2f27ee85ff4c350d217c86fd6d; historical local 14c5eb223c5780a53e9a5b5c4b47de0139908d6b. **Last verified:** 2026-10-14. **Owner/reader:** router, native inference engineer, auth/security and release reviewer. **Evidence links:** [central release hub](https://github.com/scottjoyner/knowledge/blob/docs/release-evidence-20261014/20-Projects/auto-suite/README.md), [PR #41](https://github.com/scottjoyner/auto-router/pull/41), [PR #66](https://github.com/scottjoyner/auto-router/pull/66). **Known gaps:** independent physical task→slot→token→delivery evidence, actual listener attribution and admin auth cutover.

## State boundary

- Current GitHub default source contains src/auto_router/secure_live.py (observed), and root README establishes a strict-offline gateway subordinate to AssistX task/lease and physical runtime admission authority.
- PR #41 (head 37237a0c481c9ffa33eb3f1cfe6dfe8d505b17ad) is **open draft** for protecting inherited admin endpoints and inspecting **effective in-memory** providers; mounted YAML, model directory/alias or open listener is not proof of active/resident/admitted model. The exact PR #41 patch adds `src/auto_router/effective_provider_snapshot.py` with a sanitized in-process snapshot and `@app.get("/admin/providers/effective")` guarded by `Depends(require_admin)` in `src/auto_router/main.py`; **none of that patch is presumed deployed**.
- PR #62 (981529db6b83400be6af6a1749150ff756b7285c) is **open draft** for read-only process/listener preflight. This is not a sampled-token execution witness.
- PRs #63/#64/#66 (head #66 e8b07b8f76b9c845d6012be9a928289bab220413) are **open verify-only drafts** for original signed root/attempt to native allocation set and slot lineage, not production listener.
- Exact #66-head GitHub CI [run 38233740511](https://github.com/scottjoyner/auto-router/actions/runs/38233740511) and [run 38233740564](https://github.com/scottjoyner/auto-router/actions/runs/38233740564) were reported successful 2026-10-14; their scope is disposable/synthetic, not physical output or release approval.
- P0 issues [#37](https://github.com/scottjoyner/auto-router/issues/37), [#61](https://github.com/scottjoyner/auto-router/issues/61), [#65](https://github.com/scottjoyner/auto-router/issues/65) **remain release blockers**.

## Identity and truth table

| Claim | Required direct observation | Do not infer from |
|---|---|---|
| configured | file entry within mounted config and exact running revision | enabled or routable |
| observed | timestamped response to bounded read-only discovery | admitted |
| resident | model/process/boot identity from native host | endpoint alias, route name |
| admitted | AssistX authoritative fresh signed projection with resource fencing | local provider status |
| routable | admitted + healthy local path + auth/tenant and bounded capacity | path TCP reachability |
| physically attested | original exact wire digest → actor → Phase A allocation → Phase B slot → native process/boot/binary/model → final sampled token provenance | valid synthetic signed receipt |
| delivered | tenant-bound forwarded result and final correlation with original ingress | token generation alone |

All seven predicates must be distinct in telemetry and UI. No hidden fallback to “the first healthy model”; no autoload or model download as a side effect of inspection.

## Proposed signed-v2 native proof protocol (not deployed)

~~~mermaid
sequenceDiagram
    participant C as Authenticated client
    participant R as Auto-Router ingress
    participant A as Native allocator
    participant S as Slot/process witness
    participant V as Independent verifier
    C->>R: Original request, principal, tenant, signed v2
    R->>V: Exact original body/wire digest + signed actor
    R->>A: Strict single-task forwarded request
    A-->>V: Phase A allocation set + parent links + real forward digest
    S-->>V: Phase B native task-to-slot + boot/model/binary
    S-->>V: Sampled-token and final-result events
    R-->>V: Tenant-delivered response receipt
    V-->>C: Verified / quarantine / unavailable, never invented success
~~~

**Required negative tests:** duplicate auth/proxy headers before lossy parser conversion; stale or replayed delegation nonce; actor/task parent substitution; wrong original wire digest; missing Phase A; out-of-set Phase B; multi-prompt, n>1, tool and multimodal fanout; conflicting concurrent claims to one slot; altered native boot/model/binary; wrong tenant; receipt fabricated from synthetic signing keys; parent/child rewrite; stale term; downstream delivered response mismatch. Fail closed instead of silently narrowing or adapting unsupported shapes. A syntactically correct native instrumentation hook or process name in Prism/llama.cpp is **source-only** until independently observed at actual deployed listener.

## Effective provider/admin and authentication compatibility

| Interface | Status / acceptance |
|---|---|
| OpenAI-compatible /v1/chat/completions | Root README documents offline forwarding; deployed request and admission behavior not run in this task. |
| /admin/providers/effective | **proposed in open PR #41**, auth-required; must return current effective **in-memory** providers, not mounted YAML; exact endpoint behavior and production deployment UNVERIFIED. |
| Signed v2 delegation | **draft synthetic** #63–#66; verifier, signer custody and original wire canonicalization require independent audit. |
| AssistX runtime catalog | AssistX supplies admission projection, not router-local inventory; expired or missing catalog fails closed. |
| 401/403 vs 404/409/429/503 | Independently distinguish auth, absent route, unready model, backpressure and unavailable; no success-shaped response or implicit reroute. |
| Proxy identity and tenant | Accept only configured trusted proxy attribution under fixed authenticating boundary; reject client-supplied identity in forwarded headers. |

## Safe operator preflight and rollback proposal

1. In a **read-only** maintenance window, identify process command/boot and bind paths of the actual listener; identify effective in-memory provider states only through an **already deployed authenticated** endpoint if confirmed. No network rewrite or service restart.
2. Compare each configured model alias to approved AssistX projection and native residency. Absence = deny/unknown, never load.
3. Inspect source/CI at pinned #66 head and confirm all negative fixtures are present, stable and independent from signer under test. A signed synthetic message cannot self-certify physical execution.
4. Keep cutover blocked until unrelated issue #41 auth protections and #37/#61/#65 physical attestation pass with operator-reviewed evidence. Do not use private Bonsai or other service keys in examples.
5. Rollback plan is a **proposal**: preserve previous authenticated listener revision and route mapping, replay/high-water custody, existing queued requests and tenant identity; abandon proposed cutover if rollback would roll back fencing state or allow replay.

## Safe reproducibility / diagnostics

~~~bash
git rev-parse main
git ls-files src/auto_router/secure_live.py
git grep -n 'AUTO_ROUTER_STRICT_OFFLINE' -- src config || true
# Review PR #41 and #66 exact-head GitHub Checks; do not dispatch inference/CI.
~~~

**Not executed:** native listener tests, inference, service restart, NAS intervention, network rewiring, model admission, route cutover, secret-bearing auth, graph effects, merge/tag/release. Existing [high-level design](HIGH_LEVEL_DESIGN.md) and [low-level design](LOW_LEVEL_DESIGN.md) remain design references; they cannot override live source/runtime proof.

## Unreleased notes, migration, FAQ and signoffs

- **Draft capability:** more explicit effective-provider truth and native allocation/slot verifier; **not shipping** until actual runtime proof.
- **FAQ: Is configured = loaded?** No. **Is loaded = admitted?** No. **Is dual-signed synthetic v2 = delivered?** No. **Can a failed model auto-fallback?** Not without explicit fresh authorization.
- **Migration order:** auth/tenancy and identity → broker/resource fencing → pinned native witness → client schema errors → signed iOS release; independent operator authorization needed before any live step.
- **Owner signoffs:** router, native runtime, AssistX admission, security/credential custody and independent release owner all **PENDING**.
