# Joyner model forwarding cutover-readiness report

On October 23, 2026, do not replace or stop any of the four
unauthenticated LAN/Tailnet socat forwards on the basis of HTTP 401
alone. The guarded destinations do not serve the same models.
This is a read-only, no-change preflight.

## Live model process and destination mapping

| Current lane | Existing model | Guarded candidate | Compatibility |
|---|---|---|---|
| localhost :1234 / joyner-toolcall | MiniCPM5-1B-Q4_K_M.gguf | Tailnet :18320 | Same file basename, HTTP 401 without token. Actual weight SHA256, runtime, alias contract, and user-bound receipts not verified |
| localhost :1236 / joyner-ling | Ling-3.0-tiny-abliterated-APEX-I-Compact.gguf | Tailnet :18321 | Wrong model: :18321 serves toolcall-v5-3b-combined-r2-f16.gguf, HTTP 401 without token |

Existing :1234/:1236 model lanes have four systemd-managed socat
forwards on LAN 192.168.1.79 and Tailnet 100.83.215.83. The
credential-free GET /v1/models returned HTTP 200 from the serving
host on all four forwards and 401 on the separate :18320/:18321
endpoints. Neither Internet exposure nor cross-host ACL reachability
has been tested.

A bounded static search across reachable Linux fleet nodes found no
literal references to these four addresses in most inspected roots.
Searches on xwing, OptiPlex and Lenovo hit time limits, and an older
xwing reservation artifact contains a reference. This does not prove
that any forwarded endpoint is unused.

## Resource warning

At 09:44 EDT, Joyner had ~135 MiB MemAvailable, ~72 KiB SwapFree,
10-second memory PSI full ~4.88%, and an active llama-bench process
using approximately 3.6 GiB resident RAM. By 09:46 that PID was no
longer running, PSI avg10 had fallen to 0.00 and MemAvailable
recovered to ~1.7 GiB. Swap remained effectively full.
Do not start another benchmark, model load or training task until
exclusive resource and lease witnesses are available. Do not
terminate existing workloads or swapoff to improve a cosmetic metric.

## Read-only usage

    ssh joyner python3 - < scripts/joyner_cutover_readiness.py

The command inspects only process cmdline fields, /proc/meminfo and
two bounded credential-free GET /v1/models requests. It does not
print tokens, prompts or arbitrary process command arguments, load
models, produce completions, or make system changes. Running through
SSH stdin installs no script on Joyner. Off-host execution does not
inspect or probe the target.

The output ALWAYS reports CUTOVER_HOLD with exit 78 and denies
proxy switching, service termination, router promotion and training,
even when a candidate model basename matches and its HTTP endpoint
returns 401. Resource thresholds are warnings, not permission.

## Required acceptance before a real migration

1. Inventory all dependent clients, users, and fallbacks.
2. Verify SHA-256 of actual model GGUF bytes at source and target;
   equal filename strings do not establish equal weights.
3. Verify exact model alias, chat API, stream behavior, tokenizer,
   pinned runtime, and negative cross-model substitution.
4. Prove user-bound execution receipts, two-user isolation,
   valid/invalid credential behavior and trace redaction.
5. Qualify exclusive GPU/resource lease and rollback while keeping
   existing forwards live.
6. Only then perform a controlled traffic migration after observing
   client acceptance, never on the basis of this read-only script.

Tracking: scottjoyner/auto-router issues #81 and #37.
