# Live epoch checks before synthetic effect steps — 2026-10-24

**Research-only, DRAFT, no provider/model execution authorization.**

## The tested boundary

The additive effect_step_check.sql function inspects the current
PostgreSQL primary's scope and reserved request for a specific
scope/epoch/owner/claim/projection/request/model/runtime tuple. It obtains
a short-lived PostgreSQL row SHARE lock while that SQL transaction is
running. Calls reject revoked, expired, wrong-epoch or mismatched grants;
terminal or missing reservations are also denied.

The explicit-opt-in qualify_effect_step.py --run harness uses a disposable
PostgreSQL 17 Bookworm image on Xwing. It uses network none, no published
ports, no volumes, ephemeral synthetic values and no service credentials.
Default execution remains PLAN_ONLY. The final cleanup removes the precise
disposable test container.

## Native physical evidence on Xwing

PR #75 exact tested source revision:
af0ce0b9bdfd68dd1f39f6ce208d3beddd3b1eed.

- Before revocation: three synthetic step checks permitted.
- Wrong owner/model/request: refused.
- After revocation: next synthetic step check refused.
- Regrant at epoch 2: old epoch refused; correct new reservation permitted.
- After terminal completion: further synthetic step refused.
- On stopping the isolated primary: no cached permit; next query errors.
- Untrusted SQL worker: no EXECUTE privilege on the new check function.
- 159/159 offline source tests passed on x1-370 and Xwing from pinned
  GitHub source. SQL executed only in an isolated disposable container.
- No provider requests, inference, workers, production services, or NAS
  writers/exports were activated.

## The unresolved physical effect race (NO GO)

PostgreSQL FOR SHARE serializes each database query against revocation,
but its lock is released when that query completes. A real model or streaming
HTTP operation could begin after the check and be running when the epoch is
revoked. Merely checking before each response chunk also leaves a gap between
the check result and the external effect. This experiment does not revoke
an in-progress provider request or guarantee that a subsequent response
chunk was never generated. It does not prove replicated-high-water safety
if a database primary is cloned/restored or a worker bypasses its check.

The research results explicitly report:
- decision=PASS_SINGLE_PRIMARY_STEP_OBSERVATION_NOT_PROVIDER_FENCE
- live_resource_effect_atomicity_proven=false
- cross_primary_highwater_proven=false
- execution_authorized=false
- provider_calls=0

**Next requirement:** the actual resource that can generate must hold
revocable authority and reject/terminate an effect at the effect boundary.
No database function or signed cached witness may replace this. An
independently protected monotonic high-water and cryptographically custodied,
redacted receipts are still required.

Leave Auto-Router #75 DRAFT, knowledge #57 OPEN, strict owner-mode deny in
merged #97 active, and autonomous provider dispatch on HOLD.
