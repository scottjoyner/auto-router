# Joyner physical recovery — network active, model admission HOLD

October 19, 2026, approximately 10:28–10:33 EDT.
**Read-only physical observations; no production system changed.**

## Recovery is physically real

On x1-370, Tailscale reported Joyner active with a direct path and
a Tailscale ping succeeded (about 1 ms). Pinned-SSH BatchMode to
Joyner succeeded; it reported approximately 1h47m uptime. The Tailnet
model-service health path at port 18320 returned HTTP 200 quickly.
A separate probe to its direct LAN model path on port 18320 timed
out (4-second deadline). The Tailnet success does not establish all
LAN ingress paths.

## Observed actual model processes

Process arguments were read **without printing API secrets**.
Numbers, artifacts, and endpoints below are observed, not admission
proofs, and may change after this snapshot.

| PID | UID | GGUF basename | Alias | Listener | Auth-file flag |
| --- | --- | --- | --- | --- | --- |
| 1817 | 1000 | toolcall-v5-3b-combined-r2-f16.gguf | toolcall-v5-3b-combined-r2-f16 | Tailnet:18321 | Yes |
| 1818 | 1000 | MiniCPM5-1B-Q4_K_M.gguf | server default | Tailnet:18320 | Yes |
| 56914 | 1001 | MiniCPM5-1B-Q4_K_M.gguf | joyner-toolcall | all IPv4:1234 | No |
| 56915 | 1001 | Ling-3.0-tiny-abliterated-APEX-I-Compact.gguf | joyner-ling | all IPv4:1236 | No |

**Alias mismatch:** `joyner-toolcall` at port 1234 is backed by
MiniCPM 1B, not a 3B toolcall model. This is a model-identity risk,
not evidence that an application actually received the wrong output.

Tailnet read-only probes **without Authorization headers**:
- port 18320 GET /health 200; GET /v1/models 401.
- port 18321 GET /health 200; GET /v1/models 401.
- port 1234 GET /health 200; GET /v1/models 200.
- port 1236 GET /health 200; GET /v1/models 200.

This proves selected peer-to-peer health/catalog reachability.
It does not prove an untrusted caller can generate completions, or
reach the listener from the public Internet. No generation POST was
attempted. Two all-interface listeners with unprotected catalog
responses require a separate caller/ACL and service-side
authentication audit before production admission.

## Memory and graphics

Joyner had 14GiB host memory, about 1.9GiB MemAvailable and
roughly 2.7GiB SwapFree; memory PSI full avg10 was zero.
Two AMD GPU PCI devices were visible (one discrete device
[1002:7551] and an integrated Raphael [1002:164e]). Device
visibility is not proof of free VRAM or a resident GLM process.
No 321B GLM runtime was found among the observed llama-server
processes. GPU capacity and exact model-file hash/loaded identity
require their own independent read-only witness.

## Coordination and release decision

x1-370's availability observer and incident coordinator remained
enabled and reported **HOLD**, `eligible_for_dispatch=false`,
`failover_authorized=false`, and
`model_loaded_proven=false`. Those flags must not be advanced
from Tailscale online status, HTTP health or unverified aliases.

Next: reconcile source-authoritative model/instance identifiers and
exact GGUF hashes with process start ticks, actual loaded resident
models, fresh capacity, authenticated GET/POST policy, permitted
paths and owner-bound signed request receipts. Resolve cross-account
duplicate llama processes and the misleading `joyner-toolcall`
alias through an operator-reviewed, client-first plan; do not
restart, kill, unload or rename any live service in this task.

**Decision: Joyner network back / model identity unqualified /
GLM-5.3 321B not proven loaded / production admission HOLD.**
