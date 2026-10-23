# Joyner inference-forward consumer census — 2026-10-23

The existing Joyner LAN/Tailnet model forwards on ports 1234/1236
return HTTP 200 to unauthenticated GET /v1/models from other Tailnet
nodes. The guarded 18320/18321 endpoints return HTTP 401.
See auto-router issue #81 and merged PRs #83 and #84.

The diagnostic NEVER disables any proxy or model. It performs only
bounded read-only ss socket queries, displaying aggregate observations,
not client IPs, tokens, request bodies, prompts, model outputs,
process credentials or packet contents.

Run on Joyner (maximum 60 seconds per invocation):

    python3 scripts/joyner_forward_consumer_census.py --duration 12 --interval 0.5

From x1 Fleet Commander, without installing anything on Joyner:

    ssh joyner python3 - --duration 12 --interval 0.5 < scripts/joyner_forward_consumer_census.py

The report observes established TCP client sockets on the four
LAN/Tailnet bound legacy listeners. It omits backend loopback sockets,
unrelated services and listeners. Per-run keyed hashing deduplicates
observed peer addresses without outputting identifiers or the key.
No persistent per-client identity is created and no files or traffic
logs on Joyner are changed.

The October 23 approximately 10:45 EDT physical run sampled 25
snapshots in 12 seconds: zero matching established legacy client
connections and zero socket-read errors. An empty window CANNOT
establish that the ports have no clients: short HTTP connections
could happen between samples, and infrequent consumers could use
those endpoints hours or days later. This does not establish
application/user identities or safe removal of any service.

All output is fail-closed: client_inventory_complete=false,
may_disable_forward=false, may_promote_router=false,
may_train=false, with exit 78. Wrong host or failed kernel socket
read cannot be interpreted as complete inventory.

Before any cutover: inventory dependent clients, match exact model
weights/runtime/alias, provide a Ling-equivalent authenticated
destination, validate scoped user-bound execution receipts and
negative auth controls, then demonstrate controlled client fallback.
This diagnostic grants no routing or training authority.
