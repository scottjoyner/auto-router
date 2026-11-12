# Router failure accounting vs productive Task completion

## Problem

The deployed `/api/fleet/routing-regret?limit=40` counterfactual summary was
independently observed with **40 failed attempts**, yet reported
`completed=40` as well as `failed=40`. The former was due to grouping
`status in ('completed','failed')` under the completed metric. Historical
realized-regret statistics for zero-duration failures also overstated
utilization. Draft Auto-Router PR #109 corrects disjoint transport statuses
and excludes failed or invalid latency data from realized useful value.

## Additional evidence boundary

A successful HTTP transport still does **not** prove productive model output,
correct quality, an active lease, a durable Task result, or its independent
custodian's acceptance. This follow-on adds disambiguating fields to the
counterfactual summary without breaking existing clients:

- `completed` and `transport_completed`: equal counts of `status=completed`
  router transports, **not** accepted work
- `failed`: only `status=failed`
- `terminal`: disjoint completed + failed
- `pending`: selected or otherwise nonterminal
- `accepted_task_completions`: **null**, not 0, because this ledger does not
  join authoritative AssistX receipts
- `task_receipt_verification`: **NOT_CHECKED**

The existing model-value statistics remain estimates from observed samples,
not signed token/quality evidence. Neither this report nor its hosted tests
approve runtime access, providers, credentials, GPU leases or production
inference slots.

## Release gates

The source fix must pass exact-head hosted CI and a read-only production
comparison before any operator-controlled rollout. Independent P0 holds
remain: anonymous router admin GET routes, expired signed AssistX work supply,
legacy node-agent 401 storms, server claim/session/lease transaction fencing,
physical exact-model artifact and custody, and receipt-based work acceptance.
This is a draft source-only metrics fix; no production ledger is rewritten.
