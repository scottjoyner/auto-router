# P0 #37 — immutable root ownership: disposable local integration evidence
*Observed 2026-10-11 — draft only, no production release gate crossed.*

Depends on [PR #48](https://github.com/scottjoyner/auto-router/pull/48)
stacked on [#45](https://github.com/scottjoyner/auto-router/pull/45).
The new `user_root_ledger.py` protects **one router ownership
domain sharing one SQLite DB and the same HMAC secret**. It is not
an encrypted production trace store, global fleet coordination system, or
independent provider attestation.

## Actual code boundary

When `AUTO_ROUTER_P0_USER_DELEGATION_SHADOW_ENABLED` is **explicitly
enabled** (default is off), strict-live middleware ordering remains:

1. Existing service/executor authentication.
2. Independently signed gateway per-human delegation verification:
   issuer/audience/key/model/time/tenant/subject/root/attempt.
3. **New durable root-owner reservation** before request idempotency.
4. Existing request idempotency, then the existing router.

The verifier refuses to install without a preexisting, operator-provisioned
private SQLite ownership database and **separate 32-byte HMAC key file**.
No key material is checked into Git or put directly in environment variables.
The runtime does not bootstrap a missing DB, nor does it auto-rebuild
a missing sentinel or rotate a key. The startup gate validates the
database's key-check marker, expected file owner and restrictive permissions.

The owner ledger's append-only, committed HMAC identifiers are:

| Logical identity | Unique authority | Persisted format |
| --- | --- | --- |
| Root ID | One root globally within **one shared DB** | Keyed HMAC digest |
| Owner | Same verified issuer + tenant + subject for root's lifetime | Keyed HMAC digest |
| Model | Exact authorized model stable for this root | Keyed HMAC digest |
| Attempt ID | Exactly one root and one signed token | Keyed HMAC digest |
| Signing JTI | Exactly one attempt across this DB | Keyed HMAC digest |
| Token | Exact canonical signed JWT for repeat-attempt detection | Keyed HMAC digest |

**No plaintext tenant, subject, request-body, prompt, response, token, or
provider output is stored in these reservation tables.** HMAC minimization
is **not equivalent to disk encryption** or approved P0 trace custody;
approved at-rest and backup key management remain open.

SQLite `BEGIN IMMEDIATE` serializes conflicting writers and `UNIQUE`
constraints enforce root/attempt/JTI identity. Each transaction commits
before the request proceeds to existing idempotency. Durable SQLite writes
run off the async event loop via `asyncio.to_thread`; concurrency still
requires lock/latency monitoring before real traffic. Reservation errors
deny the request before idempotency/router:

- 401 for invalid signed credentials or model scope;
- 409 for an already-used attempt, a changed owner/model, a conflicting
  attempt/root, or a reused signing JTI;
- 503 for missing, replaced, permission-unsafe, keyed-incorrect or
  inaccessible durable ownership storage.

A repeated signed attempt **cannot be forwarded a second time**, even if
the legacy idempotency TTL has expired. This is deliberately conservative:
a cancellation after durable reservation may burn an attempt before actual
model execution. A trusted gateway must issue a **new unique signed attempt**
on the original owner-bound root to retry. This is not an exactly-once
guarantee, and delivery/execution remain UNKNOWN.

## Explicit offline provisioning requirement — not a deployment instruction

The code path `RootOwnerLedger(path, secret, bootstrap=True)` exists
for disposable tests and a future approved **one-time offline
provisioning utility** only. **Production installation calls
`RootOwnerLedger(path, secret)` without bootstrap.** Missing
DB, missing keyed sentinel, insecure permissions, mismatched HMAC key,
or unavailable filesystem cause startup refusal. Keep the DB, WAL,
SHM, backups and 32-byte key in one jointly managed custody domain;
do not put active transactional SQLite on the congested USB /nas share.

This prevents *accidental* runtime recreation after a lost DB. It cannot
independently rule out a malicious rollback to an older valid snapshot
with the same secret. That requires an external append-only ledger,
monotonic checkpoint/watermark or durable consensus authority. Restoring
an old snapshot without reconciling that authority is **NOT APPROVED**.

## Tests and scope

The tests instantiate real `ExecutorInferenceAuthMiddleware`,
`UserDelegationShadowMiddleware` and `RequestIdempotencyMiddleware`
against ephemeral FastAPI apps and `tmp_path` files. Targeted coverage
includes:

- Alice and Bob using one existing gateway service bearer remain
  distinct **only when** independently signed delegated root/attempts
  are supplied; missing header retains legacy UNKNOWN-human semantics.
- Reissuing a root for a different subject/model returns 409 before
  reserving downstream idempotency; the same holds after app restart.
- Duplicate signed attempts and signing-JTI reuse are refused; new
  signed attempts under one verified root are distinct.
- SQLite reopen, fresh process, concurrent writer racing, key rotation
  mismatch, erased/corrupt key marker, missing/replaced DB, symlink,
  unsafe permissions, unsafe secret file and missing parent fail closed.
- 503 is returned before routing if a disposable owner DB disappears
  after middleware construction.
- **Negative split-brain control:** two *independent* node DBs can each
  accept conflicting Alice/Bob ownership for one UUID. This is a
  **demonstrated limitation, not an acceptance test for multi-node safety**.

Run in an isolated x1-370 worktree and disposable venv:
```sh
ruff check src/auto_router/user_root_ledger.py \
  src/auto_router/user_delegation.py \
  src/auto_router/request_idempotency.py \
  src/auto_router/secure_live.py \
  tests/test_p0_37_durable_root_owner.py \
  tests/test_p0_37_verified_user_delegation.py

python -m pytest -q \
  tests/test_p0_37_durable_root_owner.py \
  tests/test_p0_37_verified_user_delegation.py

python -m pytest -q --disable-warnings \
  -k 'not test_registry_matches_live_hosts'
```

The last broad command **explicitly deselects** the independently
observed live provider-registry drift test. It does not demonstrate
a green unfiltered CI run or confirm hosted GitHub Actions.

## Outstanding blockers before real two-human use

1. **Global root authority**: one durable, approved, linearizable root
   owner/attempt authority across nodes; signed writer identity and
   revocation, loss/rollback recovery and disaster-recovery quorum rules.
   A node-local DB replicated asynchronously is **not** safe.
2. **Real gateway/IdP**: verified two independently authenticated humans,
   permitted tenant and subject mapping, short-lived signing keys,
   gateway network boundary, key rotation and audit. Two fixture Ed25519
   users are not two people.
3. **Per-request provider process evidence**: runtime-resident model and
   authoritative observation of attempt actually executing, independent of
   SSE completion, route config or client-provided metadata.
4. **Custody/delivery**: approved encrypted producer+node+tenant+subject
   trace sink; revocation and owner-scoped read/subscription; explicit
   incomplete-stream and offline replay states; client delivery UNKNOWN
   until independently observed.
5. **Capacity**: Beelink root and IO pressure remain NO_GO; coordinate
   custody-safe remediation before a bounded physical model canary.
6. **Rollout**: hosted CI, focused security review, staging/rollback,
   separately approved access and monitored latency under contention.

The default-disabled gate changes routing eligibility and idempotency
when enabled; despite the `SHADOW` name it is **NOT read-only**.
The branch is for review only. Do not merge, deploy, or report physical
two-user receipts until all independent gates are crossed.
