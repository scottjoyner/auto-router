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

## Router execution-network proof and provider readiness fix (19:41 EDT)

- An unmodified Auto-Router container successfully made anonymous HTTP health requests to both joyner and xwing GPU endpoints. The new self-contained `scripts/fleet_gpu_router_network_probe.py` can run directly via `docker exec -i git-router-1 python3 - --timeout 3 --require-healthy < scripts/fleet_gpu_router_network_probe.py`. It reads no secrets and performs no POST or routing. On 2026-10-16 at ~19:41 EDT, all four GPU listeners returned health=200, anonymous `/v1/models`=401; `ready_listeners=4/4` and `automatic_dispatch=disabled`.
- Identified a real readiness false positive in the router's `OpenAICompatibleProvider.health()`: it treated any HTTP status below 500 as healthy, including denied credentials (401/403), redirects, and throttling. Draft fix requires HTTP 200 from authenticated model-list request, otherwise `ok=False`. No running router code was modified.
- Added negative provider health tests for 201, 204, 301, 302, 401, 403, 404, 408, 429, 500, 503 and missing API keys, plus good-200 behavior. Tests use isolated stub HTTP clients and fake credentials and do not contact GPU nodes. The new router-network script additionally proves unauthorized model-list responses fail closed if accidentally changed to HTTP 200.
- A local router virtual environment passed **54 focused tests** spanning GPU recovery, provider auth/health, existing provider mocks, and admission. The production Auto-Router container is still unchanged; the fix must pass GitHub CI and code review before deployment.
- Remaining security gates: mount appropriately restricted API credentials into the router service via approved custody, authenticate *from the router execution path*, prove model identity and owner-bound receipts including revocation, test runtime projection staleness/fencing and slot contention, and demonstrate quick disabled-provider rollback. Health/network 200/401 is NOT production admission.

## Physical-GPU shared admission gate — draft implementation (October 16, 19:50 EDT)

- Identified a cross-model double-booking flaw in the candidate configuration: each separate llama.cpp process (joyner MiniCPM and ToolCall, xwing ROCm MiniCPM and Vulkan Bonsai) had `parallel_slots: 1` but a **different runtime_instance_id** despite sharing a single GPU. Those independent runtime gates could grant two GPU-heavy requests concurrently.
- The draft models now carry `gpu_resource_group_id` (stable physical-card identity) and `gpu_parallel_slots` (zero by default, fail closed when a GPU group is specified without quota). All four **disabled** GPU candidates explicitly group by physical card: `joyner:1002-7551:gfx1201` and `xwing:1002-1586:gfx1151`, each with a shared capacity of one. Model-serving process identities are deliberately still distinct.
- The draft `RuntimeAdmissionController` holds a per-process model-runtime lease **and** a shared physical-GPU lease. A busy physical group rejects with HTTP-429-like ProviderError; it releases the runtime lease on exceptions or cancellations. An absent capacity rejects with HTTP-503-like ProviderError. Other physical GPU groups can admit in parallel.
- Signed AssistX v2 projections regenerate per-model admission controllers. Without further work, a new generation could overlook an old GPU reservation. The draft `PhysicalGpuAdmissionPool` is shared through `RuntimeProjectionManager.apply()` across in-process signed generation swaps; a changed capacity for the same GPU group is rejected without a supervised reset. Retired-controller pruning now ignores group gates shared with newer generations.
- Proof: **93 tests passed, 3 skipped** including six new direct GPU capacity tests, two new **real Ed25519-signed generation-swap tests**, plus existing executor/claim/stream and runtime projection checks. All tests are synthetic and make no live production GPU dispatch requests.
- **Important remaining NO-GO:** a memory-local GPU admission pool is **not** a distributed lock, fencing token or crash-safe reservation. Parallel Auto-Router replicas / split brain, loss of a model serving process, stale GPU memory residency and revocation after dispatch are *not* solved by this patch. A production rollout requires an externally coordinated capacity owner with fencing for multi-router safety, hardware-attested physical group identity, per-user receipts, verified secrets and supervised rollback. The `gpu_resource_group_id` must be included in an authority-signed AssistX projection; provider YAML alone cannot establish it.
- No production Auto-Router container, live provider YAML, active GPU service or CPU fallback was modified during this development.

## Cross-router Redis reservation prototype — isolated experiment (October 16, evening)

- Added a **standalone, unintegrated** Redis Lua reservation prototype in src/auto_router/gpu_distributed_lease.py. It offers atomic per-physical-card acquisition, owner-and-sequence CAS renewal and release, bounded TTLs, and fail-closed transport/error handling. The purpose is to test reservation mechanics, not to grant production dispatch authority.
- Included a disposable real-Redis runner at scripts/test_gpu_redis_disposable.sh (host-loopback 127.0.0.1:16389 only; redis:7-alpine; no persistence; 128 MiB memory, 0.5 CPU; cleanup trap). It does not touch production Redis, GPU, credentials, live Auto-Router config or services.
- **10 real/disposable Redis tests passed**, including a separate Python child process holding the R9700 reservation while the parent process is denied, then a successful reacquisition after release. Wrong-owner/stale releases, network partition, card independence, expiry and a simulated lost Redis counter were also exercised. The broader recovery suite was 105 passed, 5 skipped without a disposable Redis instance; integration tests explicitly skip unless the exact disposable URL is configured.
- **Unresolved distributed NO-GO:** expiry/release and Redis atomicity do not prevent a stale request to raw llama.cpp, and a nonpersistent Redis reset can reuse a numeric sequence. See docs/gpu-distributed-fencing-no-go.md for a minimum GPU-enforcing proxy, independently durable fencing epoch and per-user receipt architecture. The Redis prototype is not wired into RuntimeAdmissionController or the live routing path.
- All four candidate GPU providers remain disabled. No CPU fallback or deployed service was changed.

## GPU-owner proxy reference slice — source-only (2026-10-16 evening)

- New `src/auto_router/gpu_owner_proxy.py` and `tests/test_gpu_owner_proxy.py` implement/test a bounded, non-streaming **reference** proxy guard, not a deployable GPU service. The app is unconfigured by default and deliberately responds HTTP 503 `no_go` on health and dispatch; no existing worker or router route is changed.
- A trusted, independently provided OwnerAuthority protocol must mint a tenant/actor/request/model/physical-group lease with a positive epoch; a host-local backend must support cancellation/abort. Both real implementations are **absent**, so prototype tests inject fake implementations only. Literal loopback backend URL syntax alone is not a proof of firewall or process isolation.
- Source guard denies untrusted caller `user`/`metadata`/tools fields, wrong model or GPU identity, incomplete terminal evidence, invalid tokens and oversized requests. It rechecks custody before and during execution, then before returning, and requests backend abort on lost authority, timeout or cancellation. Streaming requests fail closed instead of claiming verified SSE lifecycle.
- **131 passed, 5 skipped** in the broader GPU/provider/claim/projection suite, including 26 new proxy tests. No live GPU proxy, model/credential migration, production Redis use or automatic fleet dispatch was performed.
- The remaining hard gates are in `docs/gpu-owner-proxy-contract.md`: durable external fencing epoch, independently verified owner delegation, real physical model-worker isolation and forcible termination, SSE lifecycle, per-user receipts and two-user custody. Do **not** promote this code on synthetic evidence alone.
