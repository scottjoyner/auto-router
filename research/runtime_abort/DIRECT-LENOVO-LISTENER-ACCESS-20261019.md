# Direct Lenovo model listener — acceptance and ACL gap (read-only)

Date: October 19, 2026, x1-370 research host. **NO LIVE CHANGES**.

## Observed facts

The actual user-level `fleet-cpu.service` on Lenovo remained
`ActiveState=active`, `MainPID=842593`. Its ExecStart configured
`llama-server --host 0.0.0.0 --port 1234` for the LFM2.5 1.2B
CPU model. The host's `ss -lntp` confirmed an IPv4 all-interface
listener at 1234. Its `ufw.service` reported active; the unprivileged
inspection of `ufw status` was denied ("You need to be root").
Consequently the effective firewall rule set is **not verified**.

Using Lenovo's observed Tailscale endpoint from x1-370, **without
Authorization headers or user tokens**, bounded GET requests returned:

- `GET /health`: HTTP 200
- `GET /v1/models`: HTTP 200

No actual inference request, sensitive prompt, credential, model
loading or data transfer was submitted. A 200 to these GETs proves
catalog/health reachability to one authorized Tailnet peer, **NOT**
that unauthenticated generation is allowed or that the listener is
reachable from the public Internet.

The running Auto-Router entrypoint on x1 was
`uvicorn auto_router.secure_live:app --host 0.0.0.0 --port 8088`.
Its middleware protects the four POST /v1 inference endpoints with
executor-token authentication. But that protection applies **at
Auto-Router**, not automatically to direct Lenovo llama-server
network access.

## Risk and production admission gate

If clients or workers can call the model listener directly, they may
bypass Auto-Router's per-user owner/claim, concurrency, model admission,
signed-attempt budget and epoch/effect-fence checks, regardless of how
well those controls work at the router.

Before production fallback, independently establish and test:

1. Allowlisted caller identity and protected ingress paths for the
   model service; validate actual Tailnet ACL/grants, host firewall
   and VLAN/LAN exposure. Do not equate UFW "active" with an allowlist.
2. Require an authenticated broker (or protected localhost/sidecar
   transport) for model generation; deny user-facing direct listeners
   and reject untrusted POSTs without consuming a model slot.
3. Enforce resource-side owner/claim/generation/epoch revocation and
   signed request IDs, rather than relying exclusively on the router.
4. Check both IPv4 and IPv6 interfaces, Tailnet vs LAN, error/retry
   and route fallbacks, then prove legitimate AssistX callers still
   work through an approved path.
5. Scope any change to an operator-reviewed staged cutover with
   rollback and current-client dependency inventory. **Do not**
   change `--host`, UFW, Tailscale policy, model service or power
   from this read-only research task.

Release decision: **HOLD**. This finding is a documented incomplete
trust boundary, not a claim of an exploited vulnerability.
