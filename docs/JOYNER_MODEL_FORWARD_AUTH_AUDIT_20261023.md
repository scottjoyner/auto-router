# Joyner model forwarding and authentication-boundary audit

October 23, 2026. This is a read-only inventory of the live inference
listener boundary, not permission to stop services, change routing,
load weights, generate tokens, or train.

## Current verified boundary

Two live localhost llama.cpp servers use 127.0.0.1 ports 1234 and 1236.
Four systemd-managed socat forwards bind these servers to the LAN
address 192.168.1.79 and Tailscale address 100.83.215.83 on both ports.

The October 23 inspection used ss -lntp to verify actual listening
interfaces and credential-free GET /v1/models from Joyner itself.
All four forwarded addresses returned HTTP 200. Thus model catalogs
were accessible without a credential from their bound host interfaces.

Two separately authenticated model endpoints at 100.83.215.83 ports
18320/18321 returned HTTP 401 to the same request. This proves only
a missing-credential check, not a verified user-binding or execution
receipt. Do not claim Internet exposure: external-client reachability,
firewall traversal and Tailnet grant enforcement were NOT tested.

The independent knowledge research October 23 diagnostic recorded a
59,912,192 to 926,683,136 byte R9700 VRAM increase during a synthetic
MiniCPM completion. That establishes GPU usage for this exact
Vulkan workload even though the idle GPU reports D3hot and ~57 MiB.
It does not prove exclusive GPU ownership, resource readiness or
qualified Flint 40B/training capacity.

## Operator procedure

On Joyner:

    python3 scripts/joyner_forward_auth_audit.py

Or from x1, without creating a file on Joyner:

    ssh joyner python3 - < scripts/joyner_forward_auth_audit.py

The tool sends only credential-free bounded GET /v1/models requests,
never completions or POSTs; parses read-only ss sockets, uses literal
IPv4 addresses only and prints no credentials. HTTP 200 means catalog
readable without a credential; 401/403 means missing-credential
request denied, and timeouts are UNKNOWN. Nothing here verifies
outside-host reachability.

The machine-readable report ALWAYS has status PERIMETER_NO_GO,
approved_for_routing=false, approved_for_training=false, and exits 78
even if every endpoint is guarded. This report never admits providers.

## Safe next steps

1. Inventory dependent LAN and Tailscale clients and approved policy.
2. Verify the network boundary from appropriately scoped separate
   clients without generating tokens.
3. Stage authenticated forwarding with per-user receipts and compare
   the existing 18320/18321 behavior. Require exact model identity.
4. Prove 401/403 negative tests, resource lease isolation, stale-proxy
   rejection, and fallback before any controlled cutover.
5. Do not stop current inference, swapoff, restart model services,
   toggle power, load weights or change routing during this audit.

See scottjoyner/auto-router issues #81 and #37.
