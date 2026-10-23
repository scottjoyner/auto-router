# Joyner inference: consumer census and guarded-forward admission

**Date:** 2026-10-23. **Scope:** read-only, owner-scoped physical diagnostics
from the x1-370 Fleet Commander session to Joyner. These probes are **not**
permission to disable a service, drain clients, route an unknown provider
or authorize a live maintenance window.

## Why this exists

Joyner's Radeon PRO R9700 32 GiB and Samsung 9100 PRO Gen5 SSD are
proven to run MiniCPM5, Bonsai 2 and (in an earlier, explicitly
reserved experiment) Ornith 1.5. Exact Ornith and Flint GGUF SHA-256
checks passed recently. The remaining benchmark blocker is operational:
four live inference services are already running, and an exclusive
large-model experiment must not evict unknown clients.

The existing, already-merged audit utilities (Auto-Router PRs #83, #84 and
#85) were inspected and copied to the
`joyner` account's research-admission directory, and their SHA-256
compared before execution. This follow-up adds offline negative tests,
a targeted authenticated MiniCPM functional canary and provider circuit
health diagnostics, without duplicating those earlier source tools.

## Live service and authentication boundary

| Listener | Current role | Tokenless GET /v1/models | Safe interpretation |
| --- | --- | --- | --- |
| Legacy :1234 LAN/Tailnet forwards | MiniCPM5, `scott` account | **HTTP 200** | Anonymous model catalog accessible, do not assume gated generation |
| Legacy :1236 LAN/Tailnet forwards | Ling tiny, `scott` account | **HTTP 200** | Anonymous model catalog accessible |
| Guarded :18320 | MiniCPM5, `joyner` account | **HTTP 401** | Anonymous listing refused, positive authorization untested |
| Guarded :18321 | ToolCall V5, `joyner` account | **HTTP 401** | Anonymous listing refused, positive authorization untested |

The owner/process/cgroup mapping was independently verified in
knowledge PR #194. Model basenames of legacy :1234 and guarded :18320
match, but that is only a **possible** replacement. File SHA equivalence,
alias/response contract, authorized positive completion, per-request
redacted NAS trace, caller scopes and rollback remain outstanding.
Legacy Ling :1236 has **no** equivalent guarded model.

## Actual kernel TCP census

Using only `ss -H -tn state established` on Joyner, with per-run
randomized hashes never emitted as peer IPs, an initial 12-second
sample produced:

- 25 snapshots at 0.5-second cadence, **zero socket-read errors**
- No established clients observed on the four legacy forwarding paths
  during that bounded sample
- Verdict `BOUNDED_SAMPLE_COMPLETE_NOT_CUSTODY_PROOF`, with
  `client_inventory_complete=false`,
  `may_disable_forward=false`, and `may_promote_router=false`

A second bounded sample of **45 seconds / 46 snapshots** at 1-second
cadence also recorded zero established clients, zero socket-read errors
and `client_inventory_complete=false`, `may_disable_forward=false`.
The exact credential-free JSON is archived at
`docs/joyner-r9700-receipts/forward-census-45s-20261023.json`
(SHA256 `7e32643764b315250deb7d88adb101e601ce68ec265154539015530cc734feb4`).

**No observed connections is not evidence of no clients.** Intermittent
jobs, short connections, queued agent work and direct Tailnet callers
can be missed. Obtain authoritative router, gateway and task-trace
ownership over a meaningful interval before any drain. Do not capture
prompts, headers, credentials or client addresses in published evidence.

## Reproducible fail-closed usage

Run only on Joyner, from a trusted operator account, with the
exact reviewed scripts in `/home/joyner/fleet-gpu/research-admission/`:

```bash
python3 joyner_forward_consumer_census.py --duration 12 --interval 0.5
python3 joyner_forward_auth_audit.py
python3 joyner_cutover_readiness.py
```

All three CLI entrypoints **deliberately exit 78** (HOLD), even when a
read-only sample executes correctly. Neither includes a model-generation
POST, bearer token, service-control action, firewall mutation or router
admission. Only the forward audit makes fixed, unauthenticated model-list
GETs. Do not treat the scripts' observations as fresh indefinitely.

## Next bounded operational gates

1. Accumulate source-authoritative consumer/caller receipts for the
   four ports across routine model and background-agent activity.
2. Independently verify positive authorized MiniCPM equivalence and
   trace custody before considering a :1234 → :18320 transition.
3. Provide an authenticated, exact Ling replacement before any :1236
   transition; ToolCall :18321 is not a Ling equivalent.
4. Check host RAM available, swap reserve, VRAM free, cgroup ownership
   and a reversible exclusive reservation immediately before Ornith/
   Flint model startup. Earlier full swap occupancy and four active
   servers remain NO-GO.
5. Use existing October 20 Ornith+Bonsai co-residency proof and October
   23 exact model hashes rather than scheduling redundant 47-minute
   cold loads merely to reprove model weight compatibility.

**Production routing, security posture and all four inference services
are unchanged by this work.** Separate knowledge PRs #191, #194,
and #197 contain detailed large-model reservation/fallback evidence.

## Exact MiniCPM weights and authenticated synthetic generation

Separate read-only GGUF hashing from both service owners established that
legacy :1234 and guarded :18320 use the same 688,065,920-byte model with
SHA-256 `81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa`.

A new host-locked `scripts/joyner_minicpm_guarded_canary.py`:

- Refuses non-Joyner execution or unverified/incorrect model size and
  SHA-256; requires the running guarded service's own process identity.
- Reads the existing API-key-file into its owner's local process memory.
  It does **not** print, serialize or copy the token into the receipt.
- Sends only bounded synthetic completions (16 requested tokens) to
  fixed loopback/own-Tailnet URLs, with proxy environment disabled,
  all HTTP redirects rejected, and short request timeouts.
- Saves response status, timing, token count and generated-content
  **hashes only**, never the prompt/response text or HTTP headers.
- Always refuses to permit service stop or proxy cutover.

On the final pinned invocation, the guarded service refused anonymous
model listing with **401**, accepted the authorized model listing with
**200**, and accepted a synthetic completion with **200**. Legacy :1234
also returned 200. Both services generated nonempty content with the
**same output SHA-256**, confirming matched outputs for this one input.

The acceptance fixture is **NOT PASSED**: generated content did not
satisfy the deterministic `42` arithmetic assertion and the advertised
model identifiers differ (`joyner-toolcall` versus the guarded model
path). No general inference-quality, alias-equivalence, user authorization
or performance acceptance can be claimed. The actual receipt is
`docs/joyner-r9700-receipts/minicpm-guarded-canary-20261023.json`
(SHA256 `0c5114f9a18a0ae25cf0a1e5a4d6179652c168c3e2f8599070f97f37a727d25b`).
The source running for that final canary has matching
source/remote SHA256 `4b9aedabe14a4220cee7fafe3e46dbbe09433dded24b29df227b5350fb3a953b`.

## xwing provider health and model-specific circuits

The live x1-370 router intermittently marks `lmstudio-xwing` unhealthy
because the `local/nomic-embed-text-v1.5` circuit is open, even when
the xwing LM Studio transport responds. This is from the existing
provider-wide `ok=false` circuit overlay, not proof of a physical node
outage or Ornith equivalence. The xwing LM Studio catalog separately
reported Ornith and embeddings loaded, while exact Joyner Ling/ToolCall
fallback remains unqualified.

The additive `provider_circuit_diagnostics.py` now exposes, in
provider-health metadata:

- `transport_probe_ok_before_model_circuit_overlay` — status before the
  model-specific circuit override;
- `open_model_circuit_count` and `model_circuit_overlay_active`.

Importantly, the existing router-level `ok=false` remains unchanged
when a circuit is open, and *no* model becomes admitted or eligible for
dispatch from this diagnostic change. The helper does not reset breakers
or invoke provider requests.

The live router is **not** redeployed by this research branch. Validate
backend state after a future deployment separately; passing unit tests
do not prove production behavior.
