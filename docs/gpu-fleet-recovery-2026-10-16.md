# GPU fleet recovery — 2026-10-16 EDT

**Release rule:** Both hosts have running GPU inference canaries, not production Auto-Router admission. `config/providers.gpu-canary.CANDIDATE.yaml` contains three intentionally disabled providers. Do not merge into the live provider configuration without owner-bound execution verification and reviewed credentials.

## Verified GPU runtimes

| Host | PCI / GPU | Runtime | Private endpoint | Model |
| --- | --- | --- | --- | --- |
| xwing | Radeon 8050S, 1002:1586, gfx1151 | ROCm ROCm0, -ngl 99 | 100.108.99.47:18302 | MiniCPM5-1B Q4 |
| joyner | Radeon AI PRO R9700, 1002:7551, gfx1201 | Vulkan Vulkan1, -ngl 99 | 100.83.215.83:18320 | MiniCPM5-1B Q4 |
| joyner | Same R9700 | Vulkan Vulkan1, -ngl 99 | 100.83.215.83:18321 | ToolCall V5 3B BF16 |

All endpoints are API-key authenticated. Health HTTP 200 and unauthenticated /v1/models HTTP 401 are necessary but NOT sufficient for authenticated execution/ownership proof. Older CPU-only xwing Ternary Bonsai 27B (:18270) and MiniCPM (:18301) are unchanged.

R9700 3B benchmark: pp128 3,288.12 tokens/sec, tg64 88.84 tokens/sec (one test, Vulkan1, 99 layers). Dedicated VRAM residency approximately 6,417,006,592 bytes. The `fleet-r9700-toolcall.service` is enabled in joyner user systemd, alongside `fleet-r9700-vulkan-canary.service` and `fleet-r9700-heartbeat.service`. GPU access belongs to account joyner through render-device ACLs.

## xwing OOM incident and recovery

At ~16:35 EDT xwing reported full 32 GiB swap, 1-minute load over 100, ~3.78 GiB available RAM, SSH timeouts and unavailable GPU service. Kernel recorded OOM kills at 18:13 and 18:20 EDT for two Firefox Isolated Web Co processes with 22,761,324 kB (~21.7 GiB) and 25,959,996 kB (~24.8 GiB) anonymous resident memory. This implicates the Firefox processes, NOT evidence that the GPU model caused the OOM.

By 18:41 the host recovered without reboot: load ~0.4, available RAM ~24 GiB, SSH and ROCm service restored, with ~11-12 GiB swap still used. Do not clear swap or terminate browser workloads without investigating active work.

## Inventory correction

The original reporter only matched PCI class VGA or 3D, but xwing Radeon 8050S appears as Display controller. The versioned `scripts/fleet_node_reporter_gpu.py` recognizes Display controller and independently checks the ROCm canary on private Tailnet, leaving LM Studio status distinct and always marking GPU auto-dispatch unauthorized. It was deployed to xwing as /home/scott/fleet_node_reporter.py after a timestamped backup. On x1-370 the same PCI detection fix was applied with a separate backup.

## Read-only checks
- `python3 scripts/fleet_gpu_preflight.py` checks health, auth boundary, fleet heartbeat, and memory pressure. It neither reads credentials nor launches inference; automatic dispatch is always false.
- `systemctl --user status fleet-xwing-rocm-canary` on xwing, and `systemctl --user status fleet-r9700-toolcall fleet-r9700-vulkan-canary fleet-r9700-heartbeat` on joyner.
- Confirm GPU enumeration ROCm0 or Vulkan1 and benchmark `-ngl 99` on a bounded test; do not conflate CPU-only `-ngl 0` with GPU operation.
- If xwing stalls, inspect free -h, uptime, ps sorted by RSS, and kernel OOM logs before restarting anything.
- Do not print API keys. Credentials have NOT been transferred into the router environment.

## Before canary provider admission

Require authenticated router-network model-list and completion tests, exact model identity/fingerprint, GPU residency evidence, quota and parallel-slots tests, per-user owner-bound receipts, stale/fencing/revocation safety checks, credential deployment via approved secret storage, rollback test and sustained production-like workload. Enable just one reviewed provider at a time. Do not overwrite the active auto-router config/providers.yaml; it carries unrelated in-progress edits.

## Code disposition

Isolated worktree `/home/scott/git/.worktrees/auto-router-gpu-20261016` on branch `fix/gpu-fleet-recovery-20261016`, originating from tracked `origin/main` rather than the dirty main worktree. New files are the disabled candidate config, preflight, two reporter scripts, tests and this report.
## Follow-up: live authenticated inference and specialized GPU build (19:20–19:35 EDT)

- Live recheck: xwing ROCm MiniCPM (:18302) and PrismML Vulkan Bonsai 27B (:18271) user services active; joyner Vulkan MiniCPM (:18320), ToolCall V5 3B (:18321), and 60-second node heartbeat active. Auto-Router healthy. All four endpoint health checks responded HTTP 200; anonymous model endpoints respond HTTP 401. No automatic dispatch was enabled.
- Important: joyner's R9700 VRAM counter can drop to ~59,912,192 bytes between requests even while the processes and HTTP health remain active, so VRAM alone or GET /health is insufficient to prove readiness. When an authenticated ToolCall V5 3B completion was submitted, dedicated GPU VRAM rose from 59,912,192 to 6,412,251,136 bytes. ToolCall V5 3B returned HTTP 200, 96 generated tokens, nonempty content in 1.606 seconds. Authenticated MiniCPM returned HTTP 200, 86 generated tokens, nonempty content in 0.339 seconds; GPU memory rose to 7,100,239,872 bytes with both exercised. These are warmup/smoke observations, not throughput SLAs.
- xwing's authenticated Vulkan Bonsai 27B endpoint returned HTTP 200, 29 generated tokens, nonempty content, finish_reason=stop in 2.752 seconds. It held the RADV render device. A separate earlier bounded Vulkan0 benchmark of this model passed: 45.51 prompt tokens/sec (pp32) and 22.53 generation tokens/sec (tg16), 99 layers GPU offloaded, PTQ1_0 format (~5.53 GiB).
- The standard ROCm llama.cpp binary failed to load the PTQ1_0 GGUF with 'invalid ggml type 143'. PrismML fork commit 2459f68 already includes PTQ1_0 Vulkan shaders, but shaderc v2026.4 rejected LocalSizeId under Vulkan 1.2. A **build-only** wrapper converting `--target-env=vulkan1.2` to `--target-env=vulkan1.3` fixed this. An isolated CMake/Ninja build with `-DGGML_VULKAN=ON`, `-j 3` produced a working llama-bench and llama-server. Reproducible steps are scripted in `scripts/build_xwing_prism_vulkan.sh`.
- The Bonsai 27B GPU service runs on Tailnet :18271 as `fleet-xwing-bonsai2-vulkan-canary.service`, using a local keyfile and explicit Vulkan0/GFX1151 device check. It is **NOT enabled on startup** because xwing recently experienced OOMs, and retains the original CPU service on :18270. A new candidate provider `llama-xwing-bonsai2-vulkan-canary` is present only in the separate disabled candidate YAML.
- Credential-safe probe: `scripts/fleet_gpu_authenticated_probe.py` checks authenticated model ID and chat completion without printing keys or prompt content. It accepts only the four reviewed local Tailnet GPU endpoints and does not follow HTTP redirects. Run it on the owning node with its local key-file; never transmit keys to orchestration logs or chat.
- Eight automated tests passed for GPU discovery, canary auth/health, overload, offline provider candidates, fail-closed admission, and probe URL safety. No production provider configuration, existing GPU service, or CPU fallback was replaced.
- Remains NO-GO for automatic routing until secret delivery to the router container, owner-bound receipts, fencing/stale-effect/revocation tests, route-by-exact-model verification, and concurrent load tests. Do not let the generic `lmstudio-xwing` or `lmstudio-joyner` provider implicitly discover the canaries without separate audited admission.
