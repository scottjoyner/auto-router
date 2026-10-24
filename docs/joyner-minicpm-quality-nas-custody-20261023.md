# Joyner R9700 — authenticated MiniCPM parity with NAS-first trace custody

**Observed:** Friday October 23, 2026, approximately 22:23–22:25 America/New_York
(Saturday October 24 UTC). **Verdict:** research-only; **NO CUTOVER**.

## Physical state

Joyner's Radeon PRO R9700 32GB and Gen5 SSD remain operational. Four live
`llama-server` instances stay running with no new model load, reboot,
service restart, proxy edit, firewall change, or router deployment. The
host has about 3.5–3.7 GiB MemAvailable, ~4 GiB of 4 GiB swap used
(without contemporaneous PSI pressure stalls). Ornith and Flint remain
on HOLD for a separately reserved benchmark window.

The legacy MiniCPM endpoint and guarded MiniCPM endpoint share both the
**same exact model SHA-256 and runtime executable SHA-256**:

| Evidence | Pin |
| --- | --- |
| GGUF | MiniCPM5-1B-Q4_K_M, 688,065,920 bytes |
| GGUF SHA-256 | `81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa` |
| llama-server binary SHA-256 | `150197ad1ab92b37967cef562a4f30189f64731aab3f33c7f6c8b02dac1773d9` |
| Legacy response model ID | `joyner-toolcall` |
| Guarded response model ID | Absolute-path GGUF identifier; path is **not** a stable client alias |
| Guarded anonymous model list | 401 (denied) |
| Guarded authorized model list | 200 (using its existing private api-key-file) |

Both endpoints accepted `model=joyner-toolcall` on the chat-completions
API. The alias is accepted in **requests** even though returned response
model IDs and model catalogs differ. No assumption is made about strict
alias fidelity for third-party SDKs or SSE clients.

## NAS-first request tracing — physically verified

New host-scoped tool:
`scripts/joyner_minicpm_quality_nas_receipt.py`.

Each outgoing model request is preceded by an **atomically created and
read-back-verified pending JSON receipt** on Joyner's existing CIFS
NAS mount. After each request, its completed/failed state, source role,
unique attempt ID, synthetic prompt hash, requested model alias, actual
response model ID, token usage, HTTP code, time, quality score and
generated-text SHA-256 are persisted and read back. A NAS write failure
stops any further requests. No user prompts, output text, credentials,
raw HTTP headers, or peer/client IPs are collected.

The source script verifies the running guarded PID/owner, exact GGUF hash,
key-file custody and mounted CIFS destination before any inference.
The test has **six short** requests in the standard mode; the explicit
follow-up has **four** requests, at most 256 completion tokens each.
All destinations, task prompts, model identity and request caps are
closed/source-defined. Requests are sequential, nonstreaming, deterministic
seed/temperature, with no redirects or external proxies.

Raw JSON is retained **on NAS only**, not in this Git repository.
The receipts were independently read and SHA-256 verified on
**Joyner and x1-370**:

| Run | NAS custody record, relative to `/nas/fileserver/fleet-reports/trace-custody/` | SHA-256 |
| --- | --- | --- |
| Initial 3 tasks × 2 model paths, 6 recorded requests | `joyner-minicpm-dualpath-20261024T022311Z-fa840b2e580d.json` | `7a9a97ade406f06ffb86876cf69b312e3d87c9ed6b00030ed917ad3362089f6e` |
| Capped 256-token follow-up, 2 tasks × 2 paths, 4 recorded requests | `joyner-minicpm-dualpath-20261024T022441Z-d892c1a641dc.json` | `88258fd22c70b0c9fcc09b24ef80020954c57db3fdc9ad557f9e027c59837294` |

All **10 actual inference attempts** completed with HTTP 200 and
were accounted for in those NAS receipts. The source script's final
host-side SHA256 for the long-follow-up version matched on Joyner and
x1-370 (`d43dad479801f73d8e8be9e9cf247c3be68f2841fb2cd333d8fa4ceaa81161dd`).
There are no asserted failovers, user-initiated requests or human-task
replays in these traces; none were attempted.

## Quality and exact-path parity

Every same-input/same-budget legacy-versus-guarded request produced
the **same generated-text SHA-256**. This is stronger than a server
health check but not model-quality or multi-user acceptance.

| Synthetic task | Initial cap | Initial result (both paths) | Follow-up cap | Follow-up result |
| --- | ---: | --- | ---: | --- |
| Arithmetic: 17 + 25 | 96 | FAIL, stopped at 96 generated tokens | 256 | FAIL, stopped at 256 tokens |
| JSON `{"ready":true}` | 96 | **PASS**, stopped after 35 tokens | Not rerun | — |
| Literal uppercase `READY` | 48 | FAIL, stopped at 48 tokens | 256 | **PASS**, stopped after 112 tokens |

Both paths returned HTTP 200 on all tests, and matched output hashes
for each corresponding task. The long follow-up's four raw
per-request response timings ranged approximately 267–1133 ms.
These tiny tests do not establish a throughput SLA, peak concurrent
VRAM/RAM behavior, response-format stability or first-token latency.

**Interpretation:** the literal failure was resolved by a larger
generation cap; arithmetic still fails, indicating a model/prompt
quality or long-reasoning problem. Do not substitute an unconstrained
1B toolcall model for a general reasoning service on the strength
of matched hashes.

The guarded model may be able to accept the legacy client-request
alias, but requires a **separately reviewed response/model-discovery
compatibility plan**, actual authorized consumer acceptance,
observed quality/latency SLO, per-user trace identity, and a reversible
maintenance lease before replacing legacy forwarding.

## Safety/next actions

1. Preserve `scott` legacy :1234/:1236 model services and the guarded
   `joyner` :18320/:18321 services, with no automatic alias/cutover.
2. Resolve the arithmetic quality/prompt-template issue independently;
   even if it passes, require multi-case quality/latency and real
   client trace custody before any production claim.
3. Identify all direct LAN/Tailnet legacy consumers across a meaningful
   operating window. Prior 12s and 45s TCP samples saw no connections,
   but neither proves zero clients.
4. Qualify an authenticated Ling replacement separately. The guarded
   MiniCPM/toolcall pair is not a Ling-equivalent route.
5. Establish an actual reversible, owner-scoped GPU and system-memory
   reservation for Ornith/Flint; otherwise honor the existing NO_GO.

**Security limitations:** immutable hashes and NAS readback prove byte
preservation and cross-node accessibility, **not** independently signed
user-bound authority or full production execution receipts. The runner
reads the guarded service's local secret into process memory to send
authorized requests, but never records or redistributes it. NAS CIFS
permissions and retention must be governed by the operator's separate
storage policy.
