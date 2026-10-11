# Public dashboard AssistX endpoint URL redaction — narrow draft security slice

**HOLD / REVIEW ONLY / NOT DEPLOYED**. Stacked independently on
[Auto-Router PR #41](https://github.com/scottjoyner/auto-router/pull/41).
Does NOT depend on agent-job GET draft #56 or dual-key research draft #54.

## Confirmed source and physical route status

Metadata-only GETs to currently running x1 router return HTTP **200** anonymously
for `/dashboard`, `/api/dashboard/summary`,
`/api/dashboard/ops-summary`, and `/metrics/ops`.
No live response body, configured endpoint, credentials, job records or
artifact metadata were inspected.

Source review shows that both dashboard HTML fragment templates rendered the
raw configured `assistx_tasks_url` and `assistx_event_sink_url` values.
These settings are optional string URLs, not guaranteed redacted endpoints.
If they contain URL userinfo or query credentials, copying them to a public
fragment would put those strings in an anonymous response. **No actual
secrets in current live values have been established.**

## Narrow patch and evidence

- Main dashboard summary now passes `None` instead of either raw configured URL
  into its **anonymous HTML template** context, while retaining the existing
  configuration-presence booleans.
- Ops dashboard fragment makes a shallow redacted copy of
  `build_ops_summary`'s context before template rendering. This intentionally
  preserves the existing authenticated JSON `/admin/ops/summary` handler,
  which has a separate operator-audience contract; reviewing privileged JSON
  response safety is out of scope.
- Both fragment templates now display `Configured (endpoint hidden)` or a
  missing-state label; they never reference either URL variable.
- Tests parse the Jinja templates, verify the status expressions against
  synthetic URL userinfo/query-secret markers, and inspect the two handler
  ASTs to ensure raw URL values cannot flow into these template contexts.
  No actual route requests, live data or secrets enter the tests.
- No URL fetching, model probing, inference, graph, storage or auth change.

## Deliberate non-goals and owner review needed

**This does not make the whole anonymous dashboard private.** It still
renders provider/usage/runtime/health/circuit summaries and may expose
operational topology. `/metrics/ops` remains anonymously reachable for
potential scrapers. Review audience/ingress policy and payload minimization
separately, alongside dual-stack security containment. Do **not** simply
apply `X-Admin-Token` to a browser HTML fragment: normal navigation/HTMX
cannot securely inject the shared router admin key without exposing it to
the browser. Avoid adding that key to frontend JS, query strings or cookies.

Confirm external operators/scrapers/monitoring depend on configured status
rather than raw URLs. Require an independent exact-head disposable
`secure_live` Docker check, and maintain PR #41/#56 and AssistX #156/#299
release gates. No merge/deploy authorized by this document.

## Production release status

Anonymous admin GET bypass: OPEN.
History/live credential custody: 14 matches, owner review OPEN.
Four-service admin token transport: OPEN.
Public dashboard payload privacy classification: PARTIAL.
Router candidate deployment: NOT PERFORMED.
