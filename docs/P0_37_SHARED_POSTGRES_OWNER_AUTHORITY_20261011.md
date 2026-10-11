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
