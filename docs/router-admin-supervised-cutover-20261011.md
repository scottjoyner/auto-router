# Supervised Auto-Router admin cutover: exact gates and rollback

**Status: HOLD / review only, October 11, 2026.** This file creates no deployment authority.
The running x1-370 router was observed mapping port **8088 on all IPv4 and IPv6 host interfaces**.
Anonymous `GET /admin/providers` returned **200** before the fix. Internet reachability
and actual sensitive value disclosure have **not** been established.

## What is already testable

- Source: Auto-Router draft #41 (inherited admin handlers require admin token; sanitized
  `GET /admin/providers/effective`, no live model probe).
- Client: AssistX draft #288 (authenticated provider catalog; only **404** falls back
  to authenticated old `/admin/providers`; no 401/403/5xx downgrade).
- Legacy read-only script callers: AssistX draft #290; missing token means NO admin
  request, not anonymous fallback.
- Repairs: AssistX drafts #289 and #291 (source binding, dashboard, output integrity,
  read-only receipt and gateway preflight repairs; do not merge the research bundle).
- Latest separate hosted three-way client composition: AssistX run **38148427107**,
  targeted **225 PASS**, full unit **833 PASS / 1 FAIL**, recovery **12 PASS**.
  The sole unit failure is tracked historical environment artifacts; **security issue
  AssistX #156 remains P0 NO-GO and is not waived**.

## Before changing network, tokens, deployment, or services

1. Operator validates legitimate direct LAN/Tailnet/iPhone/automation use of 8088.
   Internal `git_default` includes `git-router-1` and `assistx-api`; this is
   **not** proof that rebinding 8088 to localhost is safe for external clients.
2. Record current container image digest, exact deployment source SHA, start time,
   admin-client inventory, approved rollback image digest and rollback owner
   **privately**, without capturing environment values into public artifacts.
3. Account owners assess/rotate affected tracked credential artifacts as needed under
   AssistX #156; do not publicly commit a removed `.env` backup diff, silently
   rewrite repository history, or claim credentials were rotated.
4. Review and integrate independently owned fixes against actual main without waiving
   unchanged unit/recovery checks. Complete legitimate operator-auth acceptance.
5. Deploy verified AssistX client/legacy script authentication **before** router auth
   hardening. Verify old-router `/admin/providers/effective` HTTP 404 falls back only
   with the configured admin header; Fleet AVAILABLE != LOADED or admitted.
6. Approve a supervised router #41 image cutover and a reversible ingress policy.
   **Do not change Docker publishing or firewall rules from this documentation.**

## Read-only acceptance (run on the router host, after supervised cutover)

With no token required, probe anonymous behavior only:

```sh
python3 scripts/verify_admin_cutover_readonly.py --anonymous-only
```

Required: exit **0**, `anonymous_admin_denied=true`. Before the fix it is
expected to **fail**, which is an honest security signal.

For authorized verification, inject the existing approved administrator credential
into `AUTO_ROUTER_ADMIN_TOKEN` through the operator's secure environment, not in a
command argument, shell history, URL, Git issue or screenshot:

```sh
python3 scripts/verify_admin_cutover_readonly.py
```

Required: exit **0**, `anonymous_admin_denied=true`, and
`authenticated_snapshot=PASS`. Only loopback HTTP port 8088 is accepted.
No admin POST, provider reload, admission change, model request or route mutation
is performed. Script never prints token, response body, provider IDs or URL queries.

Supplement with separate authenticated operator UI and legitimate caller checks.
A passing read-only script is **not** proof of remote network isolation, model
residency, physical Destroyer/iPhone acceptance, request ownership or rollback.

## Fail / rollback

Stop promotion on any 200 anonymous admin response, auth downgrade, unrecognized
snapshot, owner/operator lockout, client 401/403 fallback, unavailable health,
unreviewed network restriction, or missing approved rollback source.

The supervisor restores **only the previously recorded and approved container
image/network configuration**, checks previously healthy endpoints, and records
sanitized time/status/commit witnesses. Do not infer authorization to restore
insecure WAN/LAN exposure; security owner must review the containment tradeoff.

No production container, network, credential, NAS data, model routing or device is
changed by this runbook.


## Independent remote negative acceptance (Destroyer AND Lenovo)

The localhost script is not a network-boundary or remote-client proof.
For each actual remote fleet node **after** the supervised cutover, copy or
use the reviewed `scripts/verify_remote_admin_auth_readonly.py` and run it
locally on that node, using the operator-confirmed x1-370 Tailnet IPv4:

```sh
python3 scripts/verify_remote_admin_auth_readonly.py --target-ip "$X1_TAILNET_IPV4"
```

This script accepts only an explicit literal private/Tailnet IPv4 address,
connects directly without an HTTP proxy, refuses redirects, performs **only
four anonymous GETs** (`/admin/providers`, `/admin/context`,
`/admin/circuits`, `/admin/agent-workers`), reads no response body, and
prints only anonymous HTTP status codes and an aggregate decision. It never
sends or accepts a credential. Source/tests are in draft PR #41.

Require both distinct physical-node receipts to report
`decision=PASS_REMOTE_401` and exit 0, with exact candidate source SHA
and capture time recorded without endpoint/body/token values. A 200,
403, 404, timeout, redirect or proxy path is **HOLD**, not a pass.
Treat a pre-cutover failure as expected evidence of the old router exposure.

This remote denial check is **independent** of authenticated internal AssistX
client acceptance. Do not interpret a 401 result as proof of provider
residency, model admission, legitimate caller functionality, WAN isolation,
credential custody, or the ability to roll back safely. No production
ingress or container configuration is changed by this runbook.
