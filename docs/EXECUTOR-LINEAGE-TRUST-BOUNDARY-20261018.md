# Executor lineage boundary hardening — 2026-10-18

Status: DEVELOPMENT FIX / NO DEPLOYMENT. Existing GLM and Lenovo
fallback admission remains HOLD. This patch has no service configuration,
router deployment, fleet credential or runtime projection change.

## Confirmed isolated regression

A synthetic Ed25519-valid, inference-scoped executor token accompanied by
client-controlled metadata containing a purported internal-service
identity was accepted by the inference-auth middleware in the baseline
worktree. The resulting payload carried both the token's executor
identity and the caller-provided service identity. The downstream claim
fence prioritized the service marker and returned without contacting the
live AssistX task-claim status service.

The reproduction uses ephemeral offline keys, no production token, no
network activity and no production request. The observed bypass concerns
the second live task-claim status check for callers already holding a
valid scoped token; the test does not establish anonymous bypass or
an exploitable deployment ingress.

## Change

- Treat request JSON metadata as untrusted for privileged fields
  assistx_executor and assistx_service.
- Overwrite those fields only after successful signature verification
  or after comparing the separately configured internal service token.
  Preserve ordinary unprivileged client metadata.
- The downstream task-claim fence rejects simultaneously present service
  and executor lineage, and requires an exact known internal-service
  identity rather than a bare authenticated=true boolean.
- The strict-offline router lifespan now fails at startup unless the
  executor middleware is installed and its auth-required setting is ON.
  This prevents accidentally running the compatibility main_live
  entrypoint without the secure_live authentication wrapper.
- Existing production code paths still require fresh signed projection,
  a task-claim status lookup after any admission wait, owner-scoped
  execution policy, and valid nonexpired leases.

## Tests

A self-contained Python IsolatedAsyncioTestCase harness loads only the
two changed modules and uses ephemeral Ed25519 keys. It verifies:
- Client service identity spoof is scrubbed from scoped executor metadata.
- A fake executor claim is overwritten with signed task/claim values.
- Internal-service-token calls remove user-supplied executor metadata.
- A contradictory dual-lineage payload fails closed.
- A weak, unauthenticated service marker fails closed.
- Empty bearer does not reach downstream ASGI app.
- Signed synthetic task A/B cannot swap their task/claim identities.
- Even after malicious metadata, a revoked claim causes a live-fence
  denial (isolated fake HTTP status source; no external API call).

Also added native project pytest regression cases in
tests/test_executor_auth.py and tests/test_claim_fence.py.
The full project pytest suite was NOT run in the current x1 Fleet
Commander Python environment: pydantic_settings and pytest_asyncio
are absent and test collection fails before those tests execute.
No dependencies or credentials were installed/loaded as a workaround.
Run the original project suite in a hermetic image before opening a
production release review.

## Remaining P0 work

- Verify that actual deployed router process is launched through
  auto_router.secure_live:app with required middleware, not an unwrapped
  main_live entrypoint. The patched startup guard fails closed when the
  middleware is absent or the auth-required setting is disabled. The
  Dockerfile and compose already name secure_live/auth-required, but
  these are not an attestation of the live process.
- Bind authorized execution to independent human/user ownership,
  verified source-authoritative AssistX task claims, task/jti,
  projection generation, exact model/path and per-user admission
  capacity; token agent_id alone is insufficient user authority.
- The existing attempt budget uses in-process memory. Test concurrent
  router replicas, restarts and replay protection; a per-process JTI
  counter is not a durable, authoritative replay ledger.
- Prove two independent authenticated users, revocation, cancellation,
  stream terminal receipts, response ownership and failover fencing in
  isolated staging with controlled keys and explicitly admitted models.
- Do not route real users directly to wildcard-bound Lenovo local
  llama-server until caller dependencies and access restrictions are
  reviewed; basic LAN catalog reachability does not prove protected POST.
- Preserve GLM NO-GO and no autonomous fallback while runtime projection,
  owner binding and 72h soak are outstanding.

This is an important trust-boundary fix but does NOT automatically make
any inference route safe for 24/7 production. Review and complete the
authenticated end-to-end verification before promotion.

## Additional isolated and live read-only verification

After the two local commits, the 11 offline ASGI regression tests also
passed inside a disposable git-router image container with:
- --network none, --read-only filesystem, no-new-privileges
- unprivileged user, 384 MiB RAM and 0.5 CPU, source mounted read-only
- no model tokens, service credentials, external network, or deployed router

The existing router image does not include pytest. The host sandbox also
lacks the current project's full pytest collection dependencies. Therefore
these 11 tests are **not** a substitute for the full CI integration suite.

A read-only process command-line check of the *running* git-router-1
container confirmed uvicorn was launched with
auto_router.secure_live:app. Its /health returned HTTP200, and a no-
credential GET of /admin/runtime-projection returned HTTP401. Those
observations establish entrypoint and admin endpoint behavior, but not
all inference-token, owner-binding or revocation guarantees at runtime.
No production POST test was performed and no restart was requested.

P0 next: exercise the fully authenticated staging gateway under two
genuine identities, confirm per-user source-authoritative claim ownership
and monotonic JTI/replay protection across process restart and replicas,
then run full CI before any code promotion. Fix remains local only.
