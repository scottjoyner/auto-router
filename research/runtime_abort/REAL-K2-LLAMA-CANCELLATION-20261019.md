# Real K2-Horizon 1B llama-server cancellation and stream fence canary

**2026-10-19. Research-only, NO PRODUCTION MODEL ADMISSION, NO AUTO FAILOVER.**

This is an isolated continuation of the Auto-Router owner/claim/fencing
research branch, stacked after:
- AssistX Task owner draft scottjoyner/auto-assist#413;
- Auto-Router draft #72, #73, #74, #75;
- physical x1/xwing PostgreSQL grant/receipt/epoch tests;
- local-only synthetic two-chunk worker test (commit 11e4e3fd);
- local-only default-off real router stream-effect adapter (25c74970).

Prior attempts exercised a cooperative Python worker but not an actual
model runtime. This physical test exercises CPU-only llama.cpp model
generation with the **actual OpenAI-compatible provider streaming
adapter and stream_lifecycle wrapper** from the local Auto-Router branch.

## Exactly what ran

- Host: **x1-370 only**. No inference workloads were sent to Lenovo,
  xwing, Joyner, production routers, or shared model listeners.
- Existing local 2,159,424,896-byte GGUF:
  K2-Horizon-1B-BF16. The model was **not downloaded/copied** or
  changed. The model file hash was NOT calculated; file custody and
  exact immutable artifact proof remain open.
- Existing LM Studio llama.cpp backend server version 0.7.0-dev,
  binary SHA256:
  699be04ebab1eab941867195f15ac1fbbd526b989071618a96e31c50bc14dadc.
  The older fleet-data llama-server binary version 0.3.0-dev cannot
  load this architecture ("unknown model architecture: k2-horizon");
  its failure was captured during the safe preflight and it exited.
- Each successful test launched a **new dedicated CPU-only server**
  on 127.0.0.1:12741, with one slot, two threads, no GPU layers and
  2048-token context. No external listener was published.
- The canary process was started in a private process session under
  a 12-GiB virtual address limit and positive niceness, with all stdout
  stored under a 0700 temporary directory. It was terminated after
  each trial using its own process-group PID and removed. The script
  never signals an existing LM Studio or fleet-model service.
- Preconditions require at least 12 GiB MemAvailable, a strict
  low memory-PSI threshold, an existing approved binary/GGUF path
  with bounded file size, expected hostname, and unused test port.
  One intermediate attempted repetition REFUSED before starting the
  model because current memory pressure exceeded this threshold.
  The operator did not bypass the gate; it passed later when pressure
  returned to normal.
- A synthetic counting prompt only; no real customer messages,
  proprietary tasks, credentials or downloaded datasets.

## Observed real HTTP cancellation

Each successful trial tested TWO distinct paths.

1. Direct HTTP stream-close witness: live
   /completion?stream=true generation returned three SSE events;
   the client explicitly closed its response stream. /slots became
   idle again (about 0.153 seconds). A bounded follow-up inference
   request succeeded.

2. Actual Auto-Router adapter and wrapper witness:
   OpenAICompatibleProvider.stream_completions contacted the REAL
   local model using /v1/completions. The local stream_lifecycle
   wrapper used the new default-off effect-gating interface.
   An *in-process synthetic authority* returned True for chunks
   1, 2 and 3, then refused chunk 4. A separate slot monitor verified
   that the server was STILL PROCESSING when chunk 4 was refused.
   The fourth chunk was suppressed before the downstream response;
   the wrapper invoked exactly one cancellation hook and closed the
   provider's HTTP stream/iterator. The interrupted request was marked
   possibly_accepted, NOT completed. The real server slot became idle
   about 0.152–0.153 seconds later, and a separate follow-up inference
   succeeded.

Three completed isolated trials all passed. Saved payload-free
evidence is under research/runtime_abort/evidence/:
- x1_real_1b_abort_trial_1_20261019.json
- x1_real_1b_abort_trial_2_20261019.json
- x1_real_1b_abort_trial_3_20261019.json
- aggregate_x1_real_1b_abort_20261019.json

Median time to idle after synthetic revocation: **0.152 seconds**.
Max in these three repetitions: **0.153 seconds**. Those short warm
measurements are NOT a statistical latency SLA, a GPU drain guarantee
or a 72-hour reliability soak. One intervening pressure preflight
refusal was recorded; it is not counted as a runtime pass.

## Reproduction and safety switches

From x1-370 only:

    /home/scott/git/auto-router/.venv/bin/python research/runtime_abort/qualify_llama_abort.py

Without --run, the harness prints PLAN_ONLY and does not create a
model process. Explicitly run only after reviewing machine capacity:

    /home/scott/git/auto-router/.venv/bin/python research/runtime_abort/qualify_llama_abort.py --run

For evidence, pass --output /absolute/new/safe/results.json. The
output file is created exclusively with mode 0600; an existing
destination or symlink is refused. Each new run repeats all guard
checks and removes its own process on success or error.

Hermetic safety tests:
    /home/scott/git/auto-router/.venv/bin/python -m pytest tests/test_runtime_abort_canary_research.py -q

## What this does not prove

- **NO trusted owner or epoch authority.** The test-only broker is a
  local Python object returning synthetic booleans, NOT a verified
  AssistX principal, Ed25519 execution token, signed completion
  authority, global revocation high-water or independent custody.
- **NO GPU/model-internal cancellation proof.** Slot release and
  follow-up generation demonstrate that this single llama-server
  stopped occupying its generation slot after stream close; they do
  not demonstrate all GPU kernels or downstream tool side effects
  stopped. CPU-only and test model were used.
- **NO atomic client delivery guarantee.** A chunk acknowledged in
  a shared database could still be buffered and delivered over the
  network after revocation. The streaming wrapper suppresses newly
  unauthorized chunks at its own yield boundary, not previously sent
  bytes in sockets, proxies or consumer queues.
- **NO host-independent authority.** Test ran on x1 loopback only;
  x1/xwing transaction races were separate synthetic tests on a
  disposable primary. A physical partition and real model takeover
  were not combined.
- **NO general provider/runtime equivalence.** A small K2 CPU model
  is not evidence that the Lenovo LFM runtime, Joyner GLM-5.3 or other
  models honor the same disconnect and cancellation semantics.
- **NO production path activation.** The review-only router adapter
  and owner/claim contracts are default-off, and no real trusted
  stream_effect_authority has been deployed.
- **NO production-ready registry.** The full Auto-Router suite has a
  previously identified live model-registry drift failure for the
  toolcall-v5-3b-combined-r2 alias. Do not suppress the check.

## Next safe operational gates

1. Implement independently authenticated multi-principal gateway and
   source-authoritative AssistX owner/JWT/claim-status checks.
2. Implement a broker-only PostgreSQL fencing authority with current
   epoch/claim verification and durable, independently signed receipts.
   No direct SQL grants to model workers or public web callers.
3. Bind the real provider's stream drain/abort to that trusted broker;
   verify cross-host partition/revocation, lack of newly accepted
   output, and actual model process state/stop semantics.
4. Enforce TLS/ACL restricted listeners, fail-closed absence behavior,
   signed result custody, idempotent terminal receipts, key revocation
   and independently held anti-rollback high-water checkpoints.
5. Controlled representative model/task acceptance, explicit smaller
   Lenovo capability set, rollback/fault injection and a 72-hour
   supervised production-like serving soak.

**GLM-5.3 auto admission and Lenovo failover remain HOLD.**

## Final October 19 regression result

The dedicated real-runtime safety and stream-wrapper suites: **40 passed**.
The complete Auto-Router non-integration regression on this research
branch then returned **568 passed, 9 skipped** and one unrelated
FastAPI/Starlette deprecation warning.

Importantly, the x1 model-registry drift gate that failed on previous
runs DID NOT fail this time. The source inventory can churn; one green
catalog sample does NOT resolve the previously observed served-model
drift, and the check was neither disabled nor altered.
