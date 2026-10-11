# Beelink physical model preflight — operator recovery research (2026-10-11)

**Read-only physical-host observation completed. NOT source-authoritative renewal, signed provider execution, current per-user owner binding, or production release.**

Parent: [draft auto-router PR #50](https://github.com/scottjoyner/auto-router/pull/50), [P0 recovery issue #52](https://github.com/scottjoyner/auto-router/issues/52), [issue #37](https://github.com/scottjoyner/auto-router/issues/37).

## Why a separate physical tool?

The prior operator-approved canonical Beelink projection **generation 643** briefly restored AssistX /api/router/runtime-projection HTTP 200 and Auto-Router fresh=true (one runtime, one loaded model, two paths, one capacity). That **900-second, explicitly time-limited** approval expired at **2026-10-11T17:16:38.815Z**. At 17:16:42 UTC a Fleet Commander read-only check confirmed generation 643 already expired, AssistX returned HTTP 503 and Auto-Router projection was stale again. There is **no authorization here** to renew it automatically or bump graph expiration times. Official approval evidence remains privately owned in x1 under /home/scott/.local/state/assistx-runtime-approvals (directory 0700, files 0600); nothing was copied into GitHub or NAS.

The earlier operator review had physical GGUF hash but renewal must reproduce **present** host/process/source facts. The new experimental utility does only that first step. It can run through Fleet Commander with SSH stdin; **no model inference, secrets, remote data exfiltration, approval writes or process restart**.

## Tool

`experiments/physical_gguf_preflight.py` runs **locally on the physical host**. It independently:
- Scans bounded /proc cmdline entries for llama-server executables, retaining only IDs of processes whose exact `-m/--model` GGUF contents match a separately reviewed expected SHA256 and length.
- Checks actual requested `--ctx-size`, `--parallel`, and the process cmdline before and after hashing to catch ordinary process restarts.
- Rejects more than one matching process, mismatched model, ambiguous flags, symlink final model paths, model files >2 GiB, and incomplete inventory.
- Checks only localhost 127.0.0.1:1234/api/v0/models and requires **exactly one** LM Studio LOADED model entry, with the expected public model identifier. **Catalog evidence alone is never authorization.**
- Prints only allowlisted process/pinned hash/size/aggregate evidence. No source model path, bearer token, private manifest, runtime URL or raw prompt appears in output.
- Always sets `can_auto_renew=false`, `can_dispatch=false`, `host_signed_execution_witness=false`, `fresh_request_ownership_proven=false`, and `operator_review_required=true`.

**A matched GGUF file cannot prove that the HTTP service is actually served by that PID**, nor provide cryptographic boot identity, source-owner signature, current lease or an authenticated completion receipt. Direct, independently reviewed LAN and Tailscale completion canaries are additional separate operator steps and are NOT run by this utility.

## Actual physical xwing→Beelink verification

Fleet Commander discovered the online Beelink host by its Tailscale peer identity. It streamed the exact GitHub-committed Python script via SSH stdin to Beelink, ran locally, and returned only sanitized output. No Beelink file or persistent daemon was created; the one source copy was staged under x1 temporary storage.

At **2026-10-11T17:23:33Z**, result **PHYSICAL_PRECHECK_PASSED**:

| Check | Measured |
|---|---|
| Model artifact | `liquid/lfm2.5-1.2b`, Q8_0 from earlier operator review |
| Actual GGUF SHA256 | `c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c` (MATCH) |
| Actual GGUF bytes | 1,246,255,456 |
| Matching llama-server process PID | **4783** |
| Configured model context | **8192** |
| Configured process parallelism | **4** |
| LM Studio inventory | 11 available entries; exactly **one LOADED**, matching expected model |
| Other llama-server processes | **1**, backed by a different ~84 MB file, NOT counted as the approved model |
| No signed process execution witness | Correctly **false** |
| No current owner-bound request receipt | Correctly **false** |
| No network completion canaries in this preflight | Correctly **false** |
| Source-authoritative canonical approval renewal | **NOT attempted** |

The approved manifest intentionally declares **one slot**, even though the existing process reports parallel=4. This tool does **not** convert process parallelism into admission capacity.

## Follow-on gates

1. Obtain operator-specific authorization for a **new** observation/reapproval cycle (not an indefinite or automatic lease). Recheck model hash, current process, exact running model and LAN/Tailscale completion evidence immediately before review.
2. Reconcile the four canonical evidence sources through AssistX official authorized approval transaction; no direct Cypher update or expiry override. New generation must be exactly expected canonical +1, with CAS and independently valid approvals.
3. Confirm fresh authenticated AssistX projection 200, router fresh=true and exact generation/expiry matching. Never use broad `operations_ready` or `/v1/models` as an admission proxy.
4. Keep Auto-Router issue #37 two-user physical execution receipts, legacy anonymous provider admin hardening PR #41, AssistX PR #288 and Govee/UPS dependency telemetry as separate gates.
5. Do not add a cron or condition watcher that **automatically reapproves** stale evidence. An advisory expiration watch with no mutation is an optional separate operator workflow.

## Test

    python3 -m unittest discover -s tests -p 'test_physical_gguf_preflight.py' -q

Default invocation only *inspects* the local host, but hashes up to 2 GiB of data:

    python3 experiments/physical_gguf_preflight.py \
      --expected-sha256 c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c \
      --expected-bytes 1246255456 \
      --expected-context 8192 \
      --expected-model liquid/lfm2.5-1.2b

This is a research-only physical preflight and cannot reauthorize or promote any provider. Keep stacked PRs draft-only.
