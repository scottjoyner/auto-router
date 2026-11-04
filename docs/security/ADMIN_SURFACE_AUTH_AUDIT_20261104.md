# Auto-Router administrative route authentication — research patch and security handoff

**November 4, 2026 · x1-370 · local isolated worktree · production NO-GO**

This branch inherits the narrowly reviewed isolated provider-inventory
authentication patch `e257419`. During fleet-proposals A–F validation,
an additional read-only source audit revealed the security issue extended
beyond `GET /admin/providers` to multiple unprotected administrative
read and mutation endpoints. Changes here are **local source and fixture
tests only**, not a production hotfix or permission to deploy.

## Discovery

The security module `auto_router/security.py` states the policy that
sensitive `/admin/*` endpoints require `AUTO_ROUTER_ADMIN_TOKEN`
or fail closed without a configured admin token.

A literal decorator inventory in `src/auto_router/*.py` found
**34 declared `/admin/*` GET/POST routes** (including historical route
modules not all mounted in the reconciled deployment). Before this
branch, only one declared decorator carried an explicit decorator-level
authentication dependency, although **several other route handlers
already required admin auth through a function parameter**.
Therefore '33 missing auth' must **NOT** be inferred from the
number of decorators patched; this is a defence-in-depth route-level
audit, not a claim that all 33 were anonymously exploitable.

Independent live read-only checks of the running service returned
**HTTP 200 to unauthenticated GET** requests for:

- `/admin/providers`
- `/admin/context`
- `/admin/providers/health`
- `/admin/circuits`
- `/admin/agent-jobs`

By contrast, `GET /admin/admission` returned **401**.
**Only HTTP status was observed**; no response bodies were read and
no live POST or mutation routes were called.

Other unprotected **source-level** admin handlers included
`POST /admin/circuits/reset`, `POST /admin/load`,
`POST /admin/outbox/{event_id}/delivered`,
`POST /admin/backlog/burn-down`, and refresh/probe endpoints.
They are risks requiring gating; **no live anonymous mutation
exploit is claimed**. Some historical modules may not be mounted in
the production reconciled entrypoint.

## Isolated patch

The patch adds `dependencies=[Depends(require_admin)]` on **33**
previously unguarded *decorator surfaces* in seven modules:

- `main.py` (13 route decorators)
- `main_live.py` (3)
- `service_routes.py` (7)
- `backlog_routes.py` (3)
- `cli_routes.py` (2)
- `live_model_routes.py` (3)
- `ops_dashboard_routes.py` (2)

The remaining `memory_routes.py` admin decorator already had the
required dependency. Existing function-parameter dependencies are
retained as-is. Additional imports are limited to the guard and
FastAPI's `Depends` in modules that lacked them.

The new static contract test
`tests/test_admin_surface_deny_default.py` enumerates exactly
34 declared administrative method/path combinations from the AST
and fails if any lacks the decorator-level requirement.
The count is pinned as a review trigger: adding/removing admin
routes requires explicit test update and audit. The test also sends
anonymous local in-process requests to **every currently registered**
admin method/path in the main app and requires HTTP 401. The test
uses no lifespan, real service connections, production tokens or
authorized mutation calls.

The known provider-inventory route retains positive synthetic tests
for `X-Admin-Token`, Basic and Bearer authentication. A pre-existing
synthetic backlog-dispatch integration test was updated to prove both
unauthorized denial and **authorized** behavior after the patch.
The authorized call uses a fake task source and monkeypatched
dispatch, not the real scheduler or external providers.

## Validation

```bash
cd /home/scott/git/auto-router-admin-surface-audit-20261104
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /home/scott/git/auto-router/.venv/bin/python -B -m pytest -q \
  tests/test_admin_surface_deny_default.py \
  tests/test_provider_inventory_auth_proof.py \
  tests/test_backlog_routes.py \
  tests/test_ops_admin_auth.py \
  tests/test_live_model_routes.py \
  tests/test_cli_discovery.py \
  tests/test_service_routes.py
```

Local result: **21 passed, 2 skipped, 0 failures**,
with one pre-existing Starlette TestClient deprecation warning.
The earlier narrower provider patch independently passed **12/12**
focused tests. The new patch may require updating downstream clients
and legacy tests that formerly assumed administrative endpoints were
anonymous; no fleet-wide integration or hosted CI pass is claimed yet.

## Non-negotiable rollout gates

1. Review every patched route and the exact production-mounted
   FastAPI route set (including dynamically mounted routers).
2. Verify no legitimate health/metrics surface was unintentionally
   made admin-only. Ensure all operator clients possess authorized
   credentials via approved secret management, **without printing
   or exporting secrets**.
3. Review cutover with the operator and the fleet's current
   release-admission policy; no direct rollout from this branch.
4. Run exact-head hosted CI, integration tests, and approved canary
   tests. Test both 401 without credentials and healthy authorized
   behavior on the actual deployed version. A local test PASS is
   not evidence the running service has been repaired.
5. Check any other privileged non-`/admin/*` routes separately.
   This patch covers only the declared admin namespace and does not
   claim complete API authentication assurance.
6. Keep automatic fleet background activation, model loading and
   privileged tenant-state mutations behind existing physical and
   production readiness controls.

## Relationship to six research proposals

This fix is **not** a production acceptance result for GraphISA,
SensorGraft, or an adaptive LFM3 system. It is a separate P0/P1
fleet operational security issue relevant to protected inference,
trace metadata, model inventory and admission controls.

- A: independent source authority and tenant ACL not yet validated.
- B: native typed read-plan matches 96 synthetic cases, no actual
  GraphISA codegen/ISA.
- C: locally signed high-water mirror detects a partial rollback;
  restoring both database and mirror still resurrects a prior grant.
- D: one previously validated sequential Ornith request completed
  successfully, but no blind quality, safe routing or concurrency
  release gate is passed.
- E: native columnar vector correctness proven on a frozen corpus,
  but SIMD/ROCm optimization and end-to-end performance are missing.
- F: real LoRA and meta-controller research exists, but selected
  adapter utility and unseen-task scoring are still not validated.

**Overall production NO-GO**. Nothing was merged, pushed,
restarted, loaded or deployed in this security research step.

## Follow-up: fully mounted route verification (November 4, 01:18 EST)

A separate script,
`scripts/audit_mounted_admin_gate.py`, imports each application
entrypoint in an isolated Python process with
`AUTO_ROUTER_STRICT_OFFLINE=true`. It inspects the actual FastAPI
`APIRoute.dependant.dependencies` compiled guards (rather than
assuming decorators imply protection), and sends anonymous in-process
requests to every mounted `/admin/*` method/path.

The client deliberately **does not enter the application lifespan**,
load models, dispatch tasks, call network endpoints, authenticate
mutation calls, or change production services.

- Base `auto_router.main`: **13 mounted admin methods**, all
  guard-dependent and anonymous HTTP 401.
- Reconciled `auto_router.main_live`: **19 mounted admin methods**,
  all guard-dependent and anonymous HTTP 401, including
  `POST /admin/load`, `POST /admin/circuits/reset`, and
  `POST /admin/runtime-projection`.
- The existing historical route modules are covered by the separate
  static **34 declared decorator** audit; not all are mounted by the
  reconciled runtime.
- A deliberately unprotected synthetic route causes the audit to
  **FAIL before invoking its handler**, demonstrating the check
  cannot silently bless missing guards.

Three new tests in `tests/test_admin_mounted_runtime_contract.py`
passed. This is not a test of production admission, operator credentials,
TLS, authentic fleet client compatibility or live POST side effects.
The running `GET /admin/providers` and `GET /admin/context`
still returned anonymous HTTP 200 at 01:16 EST. Do not call the
deployment fixed until an approved rollout is actually verified.

Reproduction:

```bash
AUTO_ROUTER_STRICT_OFFLINE=true PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=src python3 -B scripts/audit_mounted_admin_gate.py \
  --entrypoint main_live

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. \
  python3 -B -m pytest -q \
  tests/test_admin_mounted_runtime_contract.py \
  tests/test_admin_surface_deny_default.py
```

## Rebase against updated upstream main and Basic-auth hardening

On November 4 at approximately 01:31 EST, a read-only Git fetch
updated `origin/main` to `cf1a74b` ("Allow local-only backlog
selection without cloud fallback"). This upstream already includes
**function-parameter `Depends(require_admin)` guards** on many of
the `main.py` admin routes; consequently the older source-based
"unguarded decorators" inventory is not proof that all those
upstream handlers were anonymously accessible.

The separate release-candidate branch
`fix/admin-route-auth-hardening-20261104` starts at that updated
`origin/main`, cherry-picks the narrow and broad research patches,
and resolves overlap by retaining the existing parameter-level
guards **in addition to the new decorator-level guards**.
The guards are de-duplicated by FastAPI's dependency cache in normal
request handling. No background scheduler behavior from an older
divergent branch is included.

The current upstream shared auth helper **already** enforces the
documented `admin` username for HTTP Basic and uses
`secrets.compare_digest` for constant-time token comparisons.
This branch retains those existing safeguards and adds focused
negative regression tests for wrong-username and wrong-token inputs.
No new secret-comparison or Basic-identity mechanism is claimed.

**Do not infer deployment from updated upstream source.** The
currently running local router was still returning anonymous
HTTP 200 for `/admin/providers` and `/admin/context` in
read-only status checks. This may reflect a stale deployed build,
routing/front-proxy mismatch or configuration, and the exact
deployed artifact must be checked before assigning root cause.
No service was restarted or production route changed.

## Enforceable loopback-only deployment canary

An additional script, `scripts/admin_auth_readonly_canary.py`,
provides a cutover acceptance test that never supplies credentials,
never reads response bodies, restricts all traffic to
`127.0.0.1`, and only issues GET requests to six pinned
administrative paths. The script exits **0** only when *every*
anonymous GET returns **401**. Any 200, 302, 404 or transport
failure returns a failing status.

```bash
python3 -B scripts/admin_auth_readonly_canary.py --port 8088
```

The three offline unit tests cover correct denial, one leaking route,
and rejected non-loopback/mutating inputs. On the pre-cutover running
service, the real canary returned HTTP 200 for five monitored routes
and HTTP 401 for `/admin/admission`: **CUTOVER GATE FAILED**.

This is a **negative control**, not a deployment. It does not prove
that any future authenticated operator request succeeds; authorized
client and rollback tests remain separately required.

**Production must remain unchanged until its deployment owner
approves the release and verifies the exact target artifact.**

## Container-image proof gate added to draft PR (November 4, 03:16 EST)

The PR's hosted `integration-smoke` job already builds and starts
`auto_router.secure_live:app` in a fresh Docker Compose container.
A new required CI step now calls the read-only
`scripts/admin_auth_readonly_canary.py --port 8088` **against that
running newly built container**, checking anonymous 401 for six pinned
administrative GET endpoints, rather than relying solely on
unit tests from a source checkout.

The offline mounted-route test now additionally imports the
**actual production `auto_router.secure_live:app` entrypoint**.
Because this entrypoint installs an idempotency SQLite ledger
during import (unlike `main_live`), the isolated subprocess
sets `AUTO_ROUTER_DATABASE_URL=sqlite:///:memory:` and
`AUTO_ROUTER_DATABASE_PLACEMENT_TEST_CONTEXT=true`. It never opens
a production /data ledger. No app lifespan or real mutation is run.
All **19** mounted secure_live admin paths reject anonymous calls,
including POSTs, in addition to the 13 base and 19 reconciled
checks.

This CI-only image proof does not attest production image digest,
legitimate operator-client compatibility, independent source
authority or rollback readiness. Production still needs an
explicit, controlled rebuild and an identical deployed image
canary pass before the P0 security gate is closed.
