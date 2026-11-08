# LFM3 research-only five-lane integration

This directory is an isolated, non-package, non-production integration boundary
for the five LFM3 research contracts: local stage response, free endpoint
subagent receipts, SensorGraft context, fleet performance, and evaluation.

It is NOT imported by auto_router runtime, provider discovery, or signed
AssistX admission. The setuptools configuration packages only src/ and
does not distribute this research/ directory.

bridge.build_lfm3_research_advisory accepts synthetic or independently
pre-collected evidence and returns a purely advisory status. It cannot make a
provider eligible, authenticate a signature, change a route, execute a model,
write a training weight, or authorize public ingress. A synthetic
RESEARCH_CANDIDATE status means its five contracts agreed on the supplied
synthetic inputs, not that any production-quality evidence exists.

The research implementation snapshots are copied from the standalone local
Fleet Commander LFM3 lab after independent test acceptance; they may be
reviewed as vendor snapshots before any upstream reuse. Changes to live
Auto-Router source, provider configuration, deployments and TestFlight are out
of scope. No private source is sent to hosted free model endpoints.

Local smoke:

    PYTHONPATH=. python3 -m pytest -q tests/test_lfm3_research_shadow.py

The optional GitHub workflow runs only these tests; it never uses production
credentials or network inference. Do not represent local pass as hosted CI.

## November 7 evidence hardening

The five component files are byte-identical to the frozen LFM3 lab at commit
c180b21, as checked by VENDOR_PROVENANCE.json and the integration tests.

The integration now requires model-pinned explicit stop and consistent total
tokens; real positive zero-cost session receipts on both agents; a fail-closed
64-record memory batch cap enforced before invoking the trusted verifier;
unique benchmark observation IDs; and an approved external witness metadata
binding to the exact candidate and baseline run/checkpoint and workload IDs.

The checks are *research interface contracts only*. Neither external cryptographic
signature verification nor independent provider billing or measurement sampling
is established by these synthetic inputs. Production admission remains HOLD.

13 isolated synthetic integration tests passed locally. Hosted exact-head CI is
a separate confirmation and must not be claimed until it executes.

## Signed evaluation attestation (research protocol v1)

The research advisory now requires an Ed25519-verified signed claim as well as
its previous bound-witness metadata. No unsigned/bare-digest approval passes.
Signature verification uses the caller-pinned Ed25519 public key, exact complete
candidate and frozen baseline JSON SHA-256s, domain separation, and an explicit
externally supplied minimum epoch. Test private keys are generated in memory,
never used for production. The local cryptographic verifier has no signer API.

This research protocol is NOT native SensorGraft/SGNC proof and is NOT evidence
of external trusted key custody or anti-rollback durability. Production routing
and release remain HOLD. The scoped workflow installs only pytest and
cryptography on a hosted runner; it does not contact external inference APIs.

## Bounded signed report serialization

The Ed25519 verifier preflights the actual report object tree and fails closed
on giant string leaves, unbounded node counts, invalid input types, depth greater
than 32, or encoded canonical JSON over 65,536 bytes. The canonical JSON bytes
for accepted small reports are unchanged. This protects research test runners
from allocating an entire unbounded JSON report prior to rejecting it.

The verifier remains separated from real SGNC cryptographic receipts, operator
key custody, and trusted external anti-rollback state. It grants no production
release or routing authority.

## Real local CPU sample: synthetic-only contract in PR

The source lab now has an observed five-request CPU-only GGUF trial and a
separately executed read-only SHA-256 check of the full local model file.
The raw source evidence remains on x1-370 in the lab repository; it is NOT
exported here or sent to hosted free models.

The vendored benchmark_report.py contains pure verify_local_stage_bundle,
which requires a separately supplied checkpoint SHA-256, five distinct
server receipt IDs and matching per-request native timing and usage fields.
It also checks the exact -ngl 0 CPU-only backend and rejects any assertion
of externally verified hardware acceleration or production admission.
Synthetic negative tests run without network or model loading.

Five warm requests are research only. The true server-reported median
decode speed is NOT interchangeable with the aggregator's simplistic
token-count/time-derived metric. This PR establishes no GPU/NPU performance,
independent external measurement custody, or production SLO.

## Passive GPU-client context is not LFM3 accelerator proof

The pure classify_existing_gpu_context classifier checks a matched process
identity and stable AMD DRM client with VRAM residency and historical compute
engine counters. It ALWAYS returns decision HOLD and accelerator_verified=false
even for a positive compute-counter change without an independently
authenticated request and backend witness. No service calls, shell access,
credential reads, driver operations or GPU token generation are performed.

A real Joyner passive evidence sample is held in the local fleet lab only;
only synthetic cases and pure checks ship in this PR. Joyner /v1/models
returns HTTP 401 without credentials. Do not bypass authentication or treat
-ngl 99 as proof. x1-370's measured baseline is CPU-only (-ngl 0).

## One-request LFM3 GPU counter correlation candidate (never admission)

The pure assess_gpu_request_correlation helper requires an externally supplied
GGUF SHA-256, a single stopped/bounded READY request, stable Linux PID/start
ticks and AMD DRM client, strict ordered monotonic request/snapshot timestamps,
and a positive same-client compute-engine delta. Synthetic adversarial tests
reject process recycling, retry, mismatched checkpoint, implausible time or
tokens, and unrelated GPU contexts.

**This function ALWAYS returns HOLD with accelerator_verified=false and
production_authorized=false**, including when all candidate fields match.
Unsigned local timestamps/DRM counters cannot prove independent GPU dispatch
during specific LFM3 tokens. The existing Joyner service is API-key-file
protected: do not disable auth, inspect keys or submit an unapproved request.
No real Joyner request was sent during this research change.

Raw host observations remain only in the local LFM3 research lab; this PR
contains code and public synthetic fixtures. Real SGNC verification, external
witness custody, and matched fleet benchmark remain separate unresolved gates.

## Free-route input validation and partial OpenCode subagent evidence

The vendored P2 route guard now rejects malformed HTTP status types,
ambiguous in-flight flags, unbounded route collections and invalid
provider/model receipt identity before promoting a public synthetic
route to PASS_TO_SINGLE_PROBE. No paid provider fallback or routing
configuration is changed. Frozen adversarial unit tests are run only
in research CI.

An OpenCode v2 coordinator restricted to the single named subagent
did create one child on the exact openrouter/recursive/flint-v1.0:free
endpoint, and the child edited only the research route_guard.py source.
The task never finished cleanly, and parent/child cost receipts were
incomplete. **The agent execution remains HOLD**; this code was accepted
for isolated research based on 162 passing independent local tests,
NOT as a completed $0 delegated run or real provider billing proof.
The coordinator permission experiment itself remains in the local lab,
not installed in Auto-Router production.

## Three-node pinned LFM3-QAD-Q4_0 file and matched CPU research baseline

Full file SHA-256 independently computed on x1-370, xwing, and Joyner:
43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452
(1,049,615,680 bytes). This verifies exact model-file identity, not
independent model provenance or production attestation.

An explicitly bounded, temporary loopback-only x1-370 CPU stage with
the QAD-Q4_0 model and n-gpu-layers=0 produced exactly five READY
stops, with median native server decode 47.360 tokens/s and median
end-to-end latency 198.863 ms. The temporary server was terminated
and its local port independently verified closed. This is not an
SLO or sustained benchmark. The original x1-370 Q4_K_M CPU stage
used a DIFFERENT quantization/context/thread count, so its numbers
must not be treated as an apples-to-apples comparison.

The pure assess_matched_qad_cpu_bundle checker validates self-reported
five-request consistency and cleanup *only* against a separately
supplied checkpoint digest; frozen public synthetic attack cases run
in the non-deploy research workflow. It can at most return
PASS_RESEARCH_ONLY and always denies accelerator or production
authorization. Raw host observations and temporary server log stay
in the private local lab, not this PR.

Joyner remains held on authenticated API client access and low RAM/
swap; xwing remains held on competing GPU workload and no pre-existing
LFM3 service. No GPU LFM3 inference, independent measurement custody,
native SGNC anti-rollback, or production release has been proven.

## Two-node matched LFM3-QAD CPU baseline and alias-neutral workload

Actual bounded research canaries on x1-370 and xwing used the same
full GGUF checkpoint SHA-256:
43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452.

Five genuine READY/stop completions on EACH node; temporary loopback
servers fully stopped, ports closed. X1-370 used a CPU-configured
0.7-era backend, xwing succeeded with its already installed 0.7.0-dev
HIP build but with --device none and zero GPU layers (CPU-only).
The first xwing trial using a legacy 0.3.0-dev runtime failed to boot
and attempted ZERO generations; it remains recorded HOLD locally.

Native server-reported medians, 5 three-token completions per node:
  x1-370: decode 47.360 tok/s, end-to-end 198.863 ms.
  xwing:   decode 40.323 tok/s, end-to-end 179.189 ms.
Do not infer hardware ranking or throughput SLO from these samples.

The raw HTTP request hashes differ because the temporary model alias
is host-specific, NOT because the prompt or sampling settings changed.
The new pure assess_paired_qad_cpu_bundles checks each known alias-
specific canonical request separately and binds both to normalized
checkpoint-bound workload SHA-256:
6de028cfee6d39d8a262d0a390d9bd6016f09c5d2742556edb616d2dd528bd07.

Frozen synthetic negative tests reject a copied raw request hash,
wrong checkpoint, CPU-to-GPU flag promotion, stale server cleanup,
insufficient resource headroom, invalid timings and malformed usage.
The evaluator ONLY returns PASS_RESEARCH_ONLY or HOLD and ALWAYS sets
production_authorized=false and accelerator_verified=false.

Host-specific raw evidence and server scripts remain in the private
local research lab; the PR includes only a pure validator and
synthetic fixtures. No changes under src/, provider configuration,
deployment, credentials or public ingress. Independent physical GPU
dispatch and SGNC relying-party custody remain HOLD.

## Fleet-wide GPU canary admission matrix (offline, read-only evidence)

The pure gpu_canary_gate.py evaluates sanitized, operator-controlled GPU
capacity telemetry for x1-370, xwing, and Joyner. It handles the
xwing Strix Halo gfx1151 unified-memory aperture separately from the
24GiB AMD-SMI reported shared GPU-addressable pool, and Joyner's
multiple GPU sysfs cards separately from its discrete R9700 card.

A candidate with apparently idle GPU and adequate capacity is only
a capacity *candidate*. The function ALWAYS returns HOLD with
production_authorized=false, operator_authorization_verified=false
and gpu_token_execution_verified=false; an attacker-controlled JSON
field cannot invent a physical witness or authorize an inference run.

The real read-only three-node check on Nov. 7, 2026 found **no**
safe GPU canary candidate:
- xwing AMD SMI graphics usage reported 100% with three existing
  model services; no per-model LFM3 dispatch witness.
- x1-370 reported only about 102MB free graphics VRAM and negligible
  free swap despite ample general RAM.
- Joyner had less than 1GiB free system RAM, nearly exhausted swap,
  and an API-key-file protected existing loopback LFM3 model endpoint.

The live host collector and its raw /proc/DRM telemetry are retained
ONLY in the local LFM3 research lab. This PR contains the pure
fail-closed evaluator and public synthetic adversarial tests only.
No model loads, generation requests, authentication bypass, GPU
driver changes, provider route changes, or production promotion.

## One real Deathstar Vulkan AMD GPU compute-context correlation

The local four-node QAD-Q4_0 checkpoint hash matches exactly:
43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452
on x1-370, xwing, Joyner and Deathstar.

Deathstar hosts an *existing* protected, Tailscale-bound Vulkan LFM3
service; its API key was not read, and that service was not changed.
A separate temporary, loopback-only research stage requested
--device Vulkan0 and --n-gpu-layers 1 with the pinned model.

The first bounded staging attempt correctly aborted before any
generation when memory PSI rose to 4.16% above the fixed 1% limit.
It cleaned up completely. A separately monitored cooldown showed
PSI recovering, and a single retry required three consecutive safe
post-load samples without changing the threshold.

The retry performed exactly ONE genuine READY/stop completion:
17 prompt + 3 completion tokens, 465.285ms HTTP latency.
Within the request bracket the SAME temporary process PID/starttime
and AMD Radeon RX 480 DRM client accumulated an additional
95,075,344 ns of compute-engine time. Afterward, the temporary
service was stopped and its port/log removed. The existing protected
service remained untouched.

**This is a process-correlated GPU compute context, NOT independently
attested LFM3 tensor-layer placement or a model speedup benchmark.**
The process only reported ~1180 KiB resident VRAM and no validated
startup layer-offload count was preserved, so true GPU weight placement
is still unresolved. External witness custody, native SGNC authority,
and production admission remain HOLD.

The pure gpu_request_audit.py is source-only, has no filesystem/network
access, and accepts this synthetic receipt shape only as
PASS_CORRELATED_GPU_PROCESS_RESEARCH_ONLY. It ALWAYS denies
actual_gpu_tensor_layer_placement_verified, independent_gpu_execution_attested,
and production_authorized. Frozen negative tests cover mismatched
checkpoint/process/client/counter, invalid token accounting, unsafe
resource pressure and incomplete cleanup.

Raw requests, DRM client IDs, subprocess scripts, and host logs are
retained exclusively in the x1-370 local research lab and are NOT
uploaded to this PR or hosted model endpoints.

## Deathstar: exact runtime-reported output layer 24 on Vulkan0

On November 7, 2026, a second tightly controlled, temporary, loopback-
only Deathstar LFM3-QAD-Q4_0 research stage successfully generated ONE
READY response (17 prompt + 3 completion tokens, 509.475 ms wall time).
The same temporary RX480 AMD DRM process/client showed a positive
95,901,453 ns compute-engine delta inside that request bracket.

At this time llama.cpp 0.7.0-dev verbose startup diagnostics explicitly
reported output layer 24 assigned to Vulkan0, offloaded 1/25 model
layers to GPU, final Vulkan0 model buffer 205.09 MiB and Vulkan0
compute buffer 61.44 MiB. Each layer count appeared TWICE (model
sizing pass and actual load) but both matched exactly. The strict
diagnostic aborted with zero requests in the earlier normal-log,
duplicate-summary and mistakenly over-escaped-regex stages. All four
temporary stages were fully cleaned up.

The pure assess_deathstar_runtime_layer_placement re-parses allowlisted
loader lines and cross-checks the requested layer, duplicate counts,
GPU identity, final model buffer, bounded response, stable AMD DRM
client, positive compute engine delta, resource gates and shutdown.
It returns PASS_RUNTIME_REPORTED_OUTPUT_LAYER_RESEARCH_ONLY only on a
consistent bounded report, and ALWAYS sets independent tensor
attestation and production authorization to FALSE.

These are a local runtime loader claim and kernel GPU process counter,
NOT independently signed tensor-kernel execution proof, not full
model acceleration, not a same-host CPU-vs-GPU speedup or SLO.
All local request receipts, raw host process IDs and scripts remain
in the x1-370 research lab, commit 8d62451; no such raw telemetry is
published or sent to external free model endpoints.

This PR adds only pure synthetic verification source and attack tests.
Native SGNC custody, physically nonrestorable anti-rollback, provider
billing proof, real independent execution attestation and production
release remain HOLD. No production changes, deployment or merge.

## Deathstar same-host CPU-only versus Vulkan output-layer five-request research

One temporary 127.0.0.1:19187 CPU-only server and one temporary
127.0.0.1:19186 one-layer Vulkan0 server, using the SAME installed
llama.cpp binary, QAD model checksum, 4 CPU threads, 2048 context,
temperature zero and READY task. Each completed exactly FIVE real
bounded three-output-token requests; each ephemeral server, log and
port was fully removed. The protected existing Deathstar LFM3 server
was not touched. The exact full QAD checkpoint SHA-256 remains:
43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452.

Native median five-request observations (DEATHSTAR ONLY):
  CPU only, --device none, zero offload: 23.535 decode tok/s;
     299.264 ms median end-to-end
  Vulkan0, output layer 24, offloaded 1/25: 14.695 decode tok/s;
     424.445 ms median end-to-end.
All five Vulkan requests had separately correlated positive AMD DRM
compute delta for the exact temporary PID/start ticks and GPU client.
Verbose loader evidence again reported 1/25 layers and 205.09 MiB
model buffer. This tiny, sequential three-token research sample
shows CPU-only was faster IN THIS TEST. It does NOT establish
statistical superiority, sustained throughput, a global GPU ranking,
or behavior at deeper offload depths.

The pure deathstar_pair_audit.py checks the two sanitized research
receipt shapes: source-known request template, CPU offload disabled,
five unique valid receipts per mode, exact model digest, matching
Vulkan output-layer logs, five positive per-request AMD DRM deltas,
safe capacity and complete cleanup. It always denies
statistical_superiority_established, independent_gpu_tensor_execution_attested
and production_authorized. Synthetic tests reject counter forgery,
replaced PID, CPU accidental GPU usage, wrong model, request replay,
bad timings and cleanup failures.

Direct kernel tracing is not available to non-root user (tracefs
controls/trace_pipe restricted; perf_event_paranoid=4). No privilege
change, global tracepoint mutation or protected service access
occurred. Local host-specific process and request evidence is kept in
the x1-370 lab commit a19bf63; the PR contains ONLY the pure
source and frozen synthetic tests. KEEP PRODUCTION HOLD.
