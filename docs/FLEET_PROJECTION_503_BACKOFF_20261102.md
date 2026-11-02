# AssistX runtime-projection 503 retry backoff — research candidate

Observed on x1-370 November 2, 2026: live `git-router-1` requests canonical AssistX `/api/router/runtime-projection` every ~5 seconds and receives repeated HTTP 503 while AssistX separately logs `canonical projection approval evidence is expired`. Concurrent routed embedding calls also return HTTP 503. The repeated polls do not renew signing approvals or enable safe inference.

This candidate changes only the **read-only projection polling interval**: consecutive HTTP 503 status responses back off from the configured minimum 5 seconds to 10, 20, 40 and a default cap of 60 seconds (config `AUTO_ROUTER_RUNTIME_PROJECTION_503_MAX_INTERVAL_SECONDS`, bounded to <= 300 seconds). A successfully validated/applied signed projection resets the interval to the configured base. 401 and 429 are not silently reclassified; signature/application errors remain fail-closed and retain the existing base cadence. `asyncio.CancelledError` still interrupts the loop. No rejection changes, new providers, signature bypass, or queued work are introduced.

A 503 is not proof of a particular cause. This reduces avoidable HTTP churn while an authority owner repairs the expired canonical generation. It does **not** stop unauthorized inference attempts from *other* workers, does not claim actual useful tokens, and is not a deployment authorization. Retain independent P0 inherited-admin authentication, signer/custody, claim-fencing and terminal receipt gates before any cutover.

## Independent local reviewer result

An existing read-only xwing Herder session was asked to assess this bounded poller. It suggested checking a generic HTTP 500 after a 503. The initial reviewer claim that 500s fell through the handler was **not accurate**: source code already resets the retry streak for every non-503 HTTPStatusError. Added a concrete 503→500→503→502 regression proving the base interval resets without lowering projection-signature or task-admission requirements.
