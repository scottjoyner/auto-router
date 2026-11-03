# Protected Auto-Router admin API links — browser operator release gate

**Status:** Draft UI hygiene regression associated with Auto-Router PR #41.
**Date:** October 11, 2026. **Deployment: not authorized or performed.**

## Observed risk

The router dashboard previously displayed plain clickable navigation to
`/admin/providers`, `/admin/context`, `/admin/agent-jobs`, `/admin/outbox`,
and other administrative JSON endpoints, including in its shared navigation,
quick links, status cards, health fragments and ops fragments.

PR #41 enforces an `X-Admin-Token` header on inherited `/admin` routes. A
normal browser navigation cannot attach that header, so a direct dashboard
link to a newly protected endpoint misleadingly appears functional but returns
HTTP 401. **Do not put bearer/admin tokens in links, HTML or URL queries.**

## Minimal non-authority change

- Leave every `/admin` HTTP route and authentication requirement unchanged.
- Preserve all endpoint names for operator documentation and audit, but
  render protected JSON endpoints as labeled **non-clickable API references**.
  These are explicitly marked "API / requires admin header" and carry only
  a harmless path in `data-admin-api-path` for semantic inspection.
- Keep unprivileged browser pages, health and metrics links clickable.
- Do not add browser token inputs, script-side credentials, cookies, alternate
  session gateways, admission authority, model load operations or probes.
- User-visible auth-enabled operator UI remains a separate reviewed feature.
  This patch avoids falsely suggesting that a browser click will do privileged
  work before such a UI exists.

## Source and synthetic acceptance

`tests/test_admin_ui_link_safety.py` checks that all catalog references for
`/admin/` are identified, no hard-coded clickable `/admin/` link survives in
any HTML template, and the actual Jinja page-shortcut components render
admin references only as non-clickable elements. It also checks that
`/dashboard` remains a true link and that the rendered component contains no
credential material or credential-bearing URL fields.

The existing provider snapshot/auth tests still require correct admin
credentials for the live API route. A previously observed unauthenticated
GET to `/admin/providers` on the **currently deployed** router returned 200.
That exposure remains a P0 blocker until PR #41 is **separately deployed**
with client-first migration and supervised rollback. This UI change alone
changes **no live production behavior**.

## Operator acceptance for rollout

1. Deploy the reviewed AssistX client change (AssistX PR #288) first and
   confirm available-vs-loaded evidence remains distinct.
2. Confirm all other legitimate privileged API consumers carry credentials.
   A running AssistX client was witnessed reading two existing admin endpoints
   with its configured token; other callers are not certified.
3. Review Auto-Router PR #41 and deploy with read-only acceptance: anonymous
   `/admin/providers` returns **401**, authenticated `/admin/providers/effective`
   returns sanitized schema 1 in-memory inventory, and ordinary dashboard
   pages still render with protected references clearly labeled.
4. Confirm no credential appears in browser source, links, network URL queries,
   the rendered page or operator screenshots. Keep Destroyer model port and NAS
   operations outside the deployment change.
5. Open a separate session-aware UI design for human operators rather than
   trying to bypass `X-Admin-Token` via JavaScript or hardcoded browser secrets.

No physical iPhone, outage, model residency or battery-runtime acceptance is
claimed by these UI tests.

### Independent Lenovo worker receipt

The same five standalone Jinja syntax and rendering safety tests were run on
Lenovo using its installed Python 3.12 and Jinja, without importing the
Auto-Router package initializer (which installs runtime policies). Only
`ui_pages.py`, the seven router HTML templates and the isolated test file
were transferred to a private `/tmp` directory; the tests did not call the
live router. **Five portable UI checks PASS**, and the seven templates
compile. The evidence includes SHA-256 of the reviewed source/test and the
complete seven-template tree:
`docs/evidence/router-admin-ui-lenovo-20261011.json`.
This remains source/test evidence, not an independently authenticated
human-browser or production deployment acceptance.
