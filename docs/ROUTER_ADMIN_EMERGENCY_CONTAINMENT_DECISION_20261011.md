# Emergency admin-ingress containment: owner decision gate (October 11, 2026)

**Disposition: P0 / HOLD / no change authorized by this document.** This is a
separate possible *interim* defense while the full client-first router security
cutover remains blocked. It must not be confused with a production release, a
token rotation, or proof that the old route handlers require authentication.

## What is physically established

Read-only status-only probes from **x1-370, Destroyer and Lenovo** independently
returned anonymous **HTTP 200** for five inherited administrative GET routes
(`/admin/providers`, `/admin/providers/health`, `/admin/context`,
`/admin/circuits`, `/admin/agent-workers`). Effective catalog returned 404,
consistent with the unpatched router. No response bodies, secrets, POSTs or
redirects were inspected. This proves reachability on the **private Tailnet**
only, not public WAN exposure or actual credential disclosure.
See [local receipt](evidence/router-admin-x1-precutover-six-route-20261011T165358Z.json)
and [Destroyer/Lenovo receipt](evidence/router-admin-two-remote-six-route-precutover-20261011.json).

The running router publishes its host port 8088 on IPv4 **and IPv6 wildcard
addresses**. A separately executed, [reviewed status-only four-service
verifier](../scripts/verify_router_ingress_containment_readonly.py) found:

| Running service | Shares a Docker network with router? | Configured origin |
|---|---|---|
| AssistX API | Yes | HTTP :8088, independently resolves to a private IPv4 address |
| AssistX worker | **No** | HTTP :8088, independently resolves to a private IPv4 address |
| Hermes adapter | **No** | HTTP :8088, independently resolves to a private IPv4 address |

All three real service namespaces got HTTP 401 from the *already protected*
`/admin/runtime-projection` without a token. Previous custody evidence
recorded legitimate configured-token 200 responses, without disclosing token
material. These findings show actual reachability, **not** that a hostname-only
route or a new allowlist would preserve every dependency.

The code-backed containment classifier returned
`HOLD_OWNER_CONTROLS_REQUIRED`, including:

- Wildcard IPv4 **and** IPv6 router publishing.
- Cross-Docker-network worker and Hermes paths.
- Shared administrator credential transported over plaintext HTTP.
- Actual Docker forwarding/hairpin NAT source identity **unverified**.
- Privileged effective firewall hooks/rules **unreadable** from the authorized
  unprivileged Fleet Commander session; passwordless `sudo` unavailable.
- No security-owner-approved change, guardrail or rollback window.

[Full value-free physical receipt](evidence/router-four-service-containment-hold-20261011.json).

## The next minimum owner-controlled verification

1. **Privately inspect actual ingress interception.** Authorized host/network
   administrator must identify Docker's effective IPv4 and IPv6 packet paths,
   forwarding/NAT and host-input processing. Checking UFW INPUT alone, Docker
   network membership, or guessing `DOCKER-USER` does not establish protection
   for both families or hairpin traffic. Do **not** publish a raw ruleset to PRs
   or attempt escalation from an unprivileged checker.
2. **Privately identify legitimate packet sources as seen by the router.**
   A container IP or service-network name may not equal the source seen after
   NAT. Observe the **actual configured** API, worker, Hermes and operator
   request paths, plus essential inference/data-plane consumers. Confirm
   ingress restrictions will not break Tailscale management or offline/local
   fallback. No token values, URLs containing credentials or source addresses
   need be published; only the owner-signed status summary.
3. **Choose a reversible control.** Evaluate Tailnet-side access policy,
   approved scoped dual-stack firewall filtering, or a separately reviewed
   authenticated ingress boundary. A port-level deny can block **all** router
   traffic on :8088, not just five administrative paths. None is automatically
   safe or authorized by the synthetic CI.
4. **Freeze rollback prerequisites first.** Privately capture the effective
   current policy, expected consumers, rollback owner, recovery path and
   exact fail-stop trigger. Verify independent management access before and
   after changes. Never flush firewall tables or republish Docker ports as
   an implicit quick fix.
5. **If explicitly authorized, verify containment separately from router
   authentication.** Remote unauthorized peers should no longer reach old
   admin endpoints, while named legitimate client-origin requests must
   continue to function. Network denial may manifest as timeout, reset or
   another non-200; **it is not an HTTP-401 authentication proof**.
   Exact six-route HTTP-401 checks are only valid **after** installing the
   approved authenticated router candidate.
6. **Audit and expire the exception.** Assign a rollback time window, record
   owner approval and a removal condition when the proper router cutover and
   credential rotation have been physically accepted.

## Independent release blockers

- [Auto-Router security issue #40](https://github.com/scottjoyner/auto-router/issues/40):
  unpatched inherited admin GET and POST authentication.
- [AssistX issue #156](https://github.com/scottjoyner/auto-assist/issues/156):
  historical public environment-file/credential custody, owner assignments
  and approved rotations.
- [AssistX issue #299](https://github.com/scottjoyner/auto-assist/issues/299):
  single shared admin token across four containers, plaintext private HTTP,
  and coordinated rotation/transport review.
- [Auto-Router draft PR #41](https://github.com/scottjoyner/auto-router/pull/41):
  undelivered image and private Compose synthetic parity are tested, but no
  production image has been staged or deployed; Docker storage remains near
  97% used, signed-admission evidence is separate, and postcutover x1 /
  Destroyer / Lenovo **six-route 401** acceptance remains outstanding.

**Bottom line:** The code-backed read-only tooling can explain why a specific
ingress restriction is **not yet safe to apply**. It cannot manufacture the
privileged firewall and source-NAT evidence that the operator must supply.
Keep production unchanged until that review and explicit authorization.
