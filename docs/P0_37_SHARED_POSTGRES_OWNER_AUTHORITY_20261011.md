# P0 #37 — Shared PostgreSQL root authority: end-to-end experimental gate

**Date:** October 11, 2026  
**Disposition:** DRAFT-ONLY, DISABLED BY DEFAULT, NOT DEPLOYED  
**Depends on:** [Auto-router PR #48](https://github.com/scottjoyner/auto-router/pull/48), which stacks on [#45](https://github.com/scottjoyner/auto-router/pull/45), #42, #36, #35.  
**Tracking:** [Auto-router issue #37](https://github.com/scottjoyner/auto-router/issues/37).

## What this fixes

PR #48 establishes a signed, trusted gateway-asserted actor/root/attempt and a
durable local owner ledger. That SQLite ledger cannot prevent split-brain when
two independent router nodes admit against different copies.

The new `src/auto_router/user_root_postgres.py` is an **explicit optional
PostgreSQL authority**. Multiple router workers/instances (including on
different physical hosts *if actually configured to use this one service*)
can call the same PostgreSQL **primary** to reserve immutable root/attempt
ownership. Unique constraints and transactions serialize competing writers;
the server rejects conflicting owner/model/root/attempt/JTI bindings.

### Trusted path

1. Existing executor/service auth validates the authenticated **gateway
   service**, not a human.
2. The optional Ed25519 delegation verifier validates a **gateway assertion**
   of tenant, subject, model, root and attempt. This assertion is not yet
   connected to a proven real IdP login.
3. The PostgreSQL authority atomically reserves the owner/model/root/attempt
   and globally unique gateway signing JTI **within one PostgreSQL primary**.
4. Only successful new reservations enter existing idempotency and routing.
   Duplicate/conflicting attempts fail 409; unavailable authority fails 503.
5. No provider execution, trace custody or client delivery state is promoted.

The router never silently falls back to node-local SQLite after a PostgreSQL
connection failure. Unknown/legacy callers without delegation can still use
the existing service semantics, but MUST NOT be labeled an authenticated
human for issue #37.

## Code/configuration (interface only — DO NOT APPLY TO PRODUCTION)

`AUTO_ROUTER_P0_VERIFIED_USER_ADMISSION_ENABLED` is **OFF by default**.
The inaccurate legacy `..._SHADOW_ENABLED` switch is rejected.

When **separately approved** for this experimental mode, the installed
`user_delegation.py` additionally requires:

| Configuration | Boundary |
| --- | --- |
| `AUTO_ROUTER_P0_OWNER_AUTHORITY=postgres` | Explicitly select the shared PostgreSQL backend; SQLite remains the default only for the earlier single-domain experiment |
| `AUTO_ROUTER_P0_OWNER_PG_DSN_FILE` | Absolute, owned/private file (`0600`) with a libpq DSN. Must specify `sslmode=verify-full`, pinned `sslrootcert` and named remote host |
| `AUTO_ROUTER_P0_OWNER_PG_AUTHORITY_ID` | Pin an operator-controlled authority label and compare it to stored authority metadata |
| `AUTO_ROUTER_P0_ROOT_OWNER_HMAC_KEY_FILE` | Separate 32-byte private (`0600`) HMAC key file, retained across restarts |
| `AUTO_ROUTER_P0_USER_DELEGATION_VERIFY_KEY_FILE` / `..._KEY_ID` | Independent pinned gateway Ed25519 **public** key and key ID |
| Existing `AUTO_ROUTER_INTERNAL_SERVICE_TOKEN` | Existing service bearer; **not** two-human identity |
| Existing `AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED=true` | Cannot be disabled as a side effect |

The constructor has `bootstrap=True` only for **isolated disposable
provisioning**; live installer always checks an already provisioned schema
and fixed authority/key check. It does **not** create, reset, migrate or
delete a database at startup.

The runtime role needs only: `USAGE` on its schema; `SELECT` on the
authority sentinel; `SELECT, INSERT` on immutable root/attempt tables.
It must have no `UPDATE`, `DELETE`, `CREATE` or privileged DB role.
This was exercised with a temporary, restricted PostgreSQL role.

Root, tenant+subject, model, attempt, JTI and token are persisted only as
separately domain-separated **HMAC-SHA256 digests**. There is no plaintext
prompt, response, tenant, subject, bearer or JWT stored in the authority
tables. **HMAC minimization is not disk encryption, a complete custody
solution, or proof of provider execution.**

## Disposable physical-database experiment

**Host:** x1-370, isolated checkout/venv under `/tmp`.  
**Database:** pre-existing `postgres:17-alpine` image run as an explicitly
named, loopback-only `127.0.0.1:15437` ephemeral container with temporary
storage and resource limits. Only uniquely named disposable test databases
are created/dropped; no existing PostgreSQL or NAS service was modified.
Production TLS checking is deliberately disabled **only inside** the
directly constructed loopback test objects; the live installer requires
`verify-full` and an active TLS session.

Tests in `tests/test_p0_37_postgres_authority.py` include:

- Competing Alice/Bob claims against the same root on independent PostgreSQL
  client connections: exactly one owner wins.
- Independent requests on separate owners, plus legitimate retry on the
  same root, admit without collision.
- Two **actual FastAPI router applications** using the existing
  executor-auth, verified-user-delegation and idempotency middleware but
  pointing at the same PostgreSQL authority: cross-app ownership conflicts
  return 409 before routing and no row is created in the losing router's
  idempotency store.
- Separate **Python OS process** reconnecting to the shared DB cannot steal
  an existing root.
- Same attempt UUID or gateway signer JTI cannot change owner or root.
- Reconnect with incorrect HMAC secret or authority label is refused;
  a startup attempt to bootstrap an already provisioned database fails.
- A restricted PostgreSQL runtime role can `SELECT/INSERT` and reserve,
  but lacks `DELETE` rights.
- PostgreSQL connection failure returns **503 before router forwarding**
  (no fail-open or switch to local DB).
- Private DSN file/TLS policy rejects permissive auth and fails closed
  when cleartext is offered to the TLS-required production constructor.
- **NEGATIVE split-brain control**: two entirely separate PostgreSQL
  databases with an identical authority label and HMAC key can **still**
  each admit conflicting owners. Thus a label is not a consensus or
  anti-rollback certificate. Preventing two writable primaries requires
  actual primary fencing, split-brain failover policy and external epoch
  evidence.

Earlier x1-370 run (commit `281287e4b956b9fa654afe0b9ba310c3ff24e5de`):

- PostgreSQL-specific: **15 passed** on the disposable PostgreSQL instance.
- Full isolated suite, selecting that ephemeral database and excluding only
  the separately known live-registry drift test: **533 passed, 11 skipped,
  1 deselected** in 13.46s.
- Ruff on changed files: PASS.
- **Do not claim the unfiltered suite is green** while
  `test_registry_matches_live_hosts` remains independently failing.

## Remaining truly end-to-end blockers

### Real two-human auth at the source

**Upstream owner:** [AssistX P0 identity issue #297](https://github.com/scottjoyner/auto-assist/issues/297) defines signed delegated identity from independently authenticated subjects, safe header treatment, and real gateway transport. This implementation is not contained in the present router PR.


A direct read of `scottjoyner/auto-assist` **current main**
`src/assistx/api.py` (2026-10-11) shows a `BASIC_AUTH_USER/PASS`
HTTP Basic path and an optional `TRUSTED_AUTH_HEADER` username branch.
Neither alone proves **two independently authenticated human principals**.
A trusted header is only usable for human attribution if an independent,
properly authenticated ingress strips untrusted copies, binds the user,
and prevents direct bypass. Basic auth based on one shared configured
username/password is not a two-user IdP. No production configuration or
reverse-proxy guarantees were inferred or changed.

**Required gateway evidence:** two distinct verified IdP sessions with
stable subject/tenant mapping; authenticated gateway-to-router transport;
policy-bound model handles; gateway Ed25519 key custody and rotation;
per-root issuance uniqueness; direct/bypass calls marked UNKNOWN rather
than quietly impersonating a user.

### Provider execution, custody and physical acceptance

- A signed human delegation is **request ownership**, not evidence the
  provider model processed the prompt. Current shadow observer tests are
  synthetic only. Need independently corroborated real provider process,
  resident instance, root/attempt, and terminal outcome.
- Client delivery must remain **UNKNOWN** until observed by the client;
  stream `[DONE]` is not delivery or process attestation.
- Approved encrypted tenant+subject+producer+node trace custody and
  per-user read/subscription authorization, producer signatures, replay,
  revocation and outage reconciliation are not in this PR.
- A single PostgreSQL primary is not a highly available consensus cluster.
  Rollback to an old valid snapshot, compromised primary, replicated
  multi-primary writes, unverified failover epoch, and off-Tailnet/partitioned
  worker behavior still require externally fenced release design.
- **Beelink physical canary remains NO_GO:** root ext4 ~3 GiB available,
  severe I/O pressure and ongoing NAS recovery. No model was loaded or
  queried in this experiment; no iPhone install/physical A/B proof exists.
- Production CI and approved staging rollout are still required.

**Release stop:** Keep this pull request, #48 and all ancestors **draft**.
No production router, gateway, NAS, IdP, signing key, owner DB or physical
provider was modified. The disposable container is not a production service.


Additional default-off installer regression tests in
`tests/test_p0_37_postgres_installer.py` verify five fail-closed cases:
feature disabled; missing private DSN file; unsafe file permissions;
non-verified TLS setting; and unsupported authority backend. Each rejects
without contacting a production database or promoting a human identity.

## Latest exact-head fleet verification (2026-10-11)

Verified on x1-370 at commit `a24f0b56626807b0580e980fc3963218cf210e16`,
before this documentation-only evidence update:

- Ruff on the shared PostgreSQL authority, middleware integration and
  PostgreSQL test modules: **PASS**.
- Disposable PostgreSQL-specific tests: **15 passed**.
- Disabled-by-default PostgreSQL installer safety tests: **5 passed**.
- Broad isolated suite with disposable PostgreSQL explicitly enabled:
  **538 passed, 11 skipped, 1 deselected**, 11.51 seconds.
- The one deselection remains the independently failing
  `test_registry_matches_live_hosts` inventory-drift check. **The
  unfiltered full suite is NOT green**.
- CI at GitHub has not independently established the exact-head hosted run.
- Neither AssistX production authentication nor a physical LM Studio
  inference request was exercised. No production database was queried or
  mutated. This result does not authorize a two-real-human release.

**Upstream identity dependency:** AssistX
[issue #297](https://github.com/scottjoyner/auto-assist/issues/297) tracks
independently authenticated human ingress, safe trusted-header treatment,
and gateway-signed per-user delegation. Until that real trusted boundary
is proven, even a consistent PostgreSQL reservation is only a signed
gateway assertion, not verified attribution to a human.

## Cross-repository signed-IdP-to-shared-Postgres experiment

The optional `tests/test_p0_37_cross_repo_oidc_pg.py` runs **only** with
explicit `P0_37_TEST_EPHEMERAL=1`, a disposable loopback PostgreSQL DSN,
and `P0_37_TEST_ASSISTX_OIDC_MODULE` pointing to the checked-out
[AssistX draft #298](https://github.com/scottjoyner/auto-assist/pull/298)
module. Without those gates it skips.

A real `cryptography` RS256 test IdP signs two distinct, fictional A/B
subjects. The AssistX gateway module verifies both identities through a
pinned test JWKS and static roster, creates exact Ed25519 router
delegations, and forwards **only generated** request headers and bounded
model JSON through HTTPX ASGITransport to two independent FastAPI router
instances. Those instances use actual executor authentication, the PR #48
verified-delegation middleware, the same disposable PostgreSQL primary
and existing idempotency middleware. The model endpoint is a **stub**
with no actual inference.

**x1-370 observed:** cross-repository test **1 PASS** at router code SHA
`c5f5f77ad5adbfdaeb01fc033d36d661e7fde873`, before the
subsequent AssistX HTTPX `trust_env=False` hardening. The test proves:

- Both distinct *test* IdP subjects are forwarded as different validated
  actor scopes; both requests are accepted through one shared authority.
- No caller-supplied `user`, `metadata`, or an unknown IdP subject is
  promoted to verified identity or forwarded.
- Missing shared authority returns **503 before routing**; no local
  SQLite fallback or unverified provider retry occurs.
- `provider_execution_attested` and `response_delivered` do not appear
  in verified actor scope, and stub success is **not** physical inference
  evidence.

This is an inter-repository software boundary experiment, **not** a
live multi-host deployment, real human authentication, physical-device test,
or encrypted trace-custody acceptance. The user-facing gateway route and
any production model provider have not been exercised end-to-end.


## Signed request provenance v2 and immutable retry scope (latest)

A security review of the complete AssistX-to-router path found that the
original v1 gateway delegation bound a subject/root/attempt/model but **not
the prompt bytes or destination endpoint**. This is addressed in the
new versioned `p0-user-shadow-v2` delegation:

- Gateway (draft AssistX PR #298) signs `request_path` and
  `request_sha256`, calculated over a domain-separated canonical JSON
  request. For the experimental gateway, only
  `/v1/chat/completions` is permitted.
- Actual strict-live Auto-router installation now **requires v2**; v1
  remains accepted only in deliberately legacy fixture contexts where
  `require_body_digest=False`. Do not enable a production v1 issuer.
- The **outer** `ExecutorInferenceAuthMiddleware` checks bounded
  delegated request JSON and captures the original canonical digest in
  an ASGI-only trusted scope **before** inserting authenticated service
  metadata. The downstream verifier uses this original digest; stripping
  middleware-added fields from a mutated prompt would be unsafe.
- Duplicate JSON field names and over-256-KiB delegated requests now fail
  closed at the outer executor-auth boundary, before root reservations.
- The PostgreSQL primary **and** local disposable SQLite ledger now retain
  a keyed `request_scope_hash` for each immutable root. A fresh signed
  attempt under an existing owner/model/root is allowed only for the same
  signed prompt and endpoint; changing either yields a **409**. No raw
  prompt or plaintext digest is stored in ownership records. Existing
  SQLite schema without that column is refused rather than silently
  migrated/recreated.
- A fixed SHA-256 golden-vector test appears in **both** repositories,
  so their separate CI suites catch incompatible delimiter/canonicalization
  changes. One cross-repo test simulates an intermediary changing the prompt
  **after** legitimate gateway signing: the router responds **401 before
  idempotency or handler execution**.

The integrated test originally detected a real serialization issue:
AssistX's gateway escaped the byte-domain separator differently from
Auto-router. The fixed golden vector and cross-repository test now pass;
this failed intermediate experiment must **not** be represented as a
passing protocol before the correction.

### Latest x1-370 evidence (2026-10-11)

- Tested router code SHA:
  `d7080d9906e17a55e270da28131a27e85d6b7be8` (before this
  documentation-only update).
- Ruff on edited router modules and regression modules: **PASS**.
- Full isolated suite with disposable PostgreSQL and AssistX gateway
  test module: **554 passed, 11 skipped, 1 deselected** in 6.07 s.
- AssistX gateway SHA
  `0e99c6e301f6839bcf5cb2548e3874b2558cc46d`:
  **21 focused tests passed**, Ruff passed, and hosted isolated identity
  workflow [passed](https://github.com/scottjoyner/auto-assist/actions/runs/38181519239).
  General AssistX CI remains red.
- The sole Auto-router deselected test was checked **separately**, read-only
  and reproduced a real mismatch: configured
  `lmstudio-x1-370` advertised
  `qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled` but
  the runtime's twice-checked model inventory did not serve it. That
  **test failed**, rather than becoming an approval to autoload a model,
  edit the registry, or waive the drift gate.
- The protocol tests used disposable IdP signatures, a disposable
  loopback-only PostgreSQL primary and stub completion handlers.
  **No real human IdP login, physical inference process, client delivery,
  encrypted trace sink, NAS custody or multi-primary fencing was proven.**
  All previously documented STOP conditions remain binding.

This signature binds the **authorized request input**, not a guarantee
that a model executed that input. Provider process/instance attestation,
terminal disposition and external client delivery must remain separate.
