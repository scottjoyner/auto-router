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

## Exploratory Deathstar RX480 Vulkan offload-depth sweep: 0/1/6/8/10

Exact LFM3-QAD-Q4_0 GGUF SHA-256 pinned and matched on four nodes:
43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452.
On Deathstar, the same installed llama.cpp 0.7.0-dev binary, four CPU
threads, 2048 context, single slot, zero-temperature READY public
prompt were held constant across **five bounded requests for each of
five modes**. CPU used --device none with GPU layers zero; Vulkan0
research stages requested exactly 1/6/8/10 layers. Each GPU stage
reported its corresponding loaded output/repeating layers in the
llama.cpp verbose model loader and positive compute-engine delta on
the exact AMD GPU client during EACH of its five requests. Temporary
servers, logs and ports were fully cleaned; the protected existing
Deathstar server was untouched.

Native median decode tok/s; median HTTP latency ms:
- CPU (0 layers): 23.535 tok/s, 299.264 ms
- Vulkan 1 layer: 14.695 tok/s, 424.445 ms
- Vulkan 6 layers: 28.736 tok/s, 359.977 ms
- Vulkan 8 layers: **28.999 tok/s, 317.423 ms**
- Vulkan 10 layers: 26.317 tok/s, 381.405 ms

Eight layers was the **best observed** tested Vulkan decode depth
and HTTP-latency depth *in this short experiment*, not a proven
performance optimum. CPU-only still had the lowest observed median
HTTP latency. Trials were sequential, not randomized, each only five
3-output-token responses; no confidence or sustained-throughput
claims, and no inference about untested depths or other hardware.

Pure depth_sweep_audit.py validates each of the three new Vulkan
depths against allowlisted loader lines, exactly repeated assigned
layers, model and GPU identities, five distinct valid bounded
responses, same PID/start ticks/AMD client, strictly positive per-
request engine compute delta, matching cumulative counter, safe
resource envelope and complete cleanup. It composes the previous
CPU vs one-layer auditor into a five-mode research-only verdict and
ALWAYS returns statistical_optimum_established=false,
independent_tensor_execution_attested=false,
production_authorized=false. Frozen synthetic adversarial tests
cover inconsistent layer logs, counters, unsafe pressure, malformed
usage, wrong model IDs and failed cleanup.

Per-host raw receipt files, live GPU device identifiers and process
logs stay in the local x1-370 laboratory at commit 81486a6.
No production src/config/deploy changes, no keys, no inference
provider routes, no releases, no global trace privileges modified.
**KEEP PRODUCTION HOLD.**

## November 8 longer 112-token mirrored block comparison (research-only)

Deathstar's exact pinned LFM3-1.7B-QAD-Q4_0 GGUF (full SHA-256
43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452)
was exercised using the SAME installed llama.cpp binary, model alias,
loopback-only port 19188, 4 CPU threads, ctx2048, one request slot,
sampling settings and exact public integer-sequence prompt across all
three modes. All six stages used the SAME full request SHA-256, whose
model alias never differed.

Fixed mirrored, NOT randomized, block order:
CPU / Vulkan8 / Vulkan6 / Vulkan6 / Vulkan8 / CPU.
Each block launched an ephemeral server, produced 3 full 112-token
completions, and verified termination/log/port cleanup.
Thus **six 112-token generations per mode, eighteen total**.

Observed native median decode tok/s and HTTP latency in milliseconds:
- CPU no-offload: 23.127 tok/s, 5238.037ms
- Vulkan6 (six reported model layers): 29.135 tok/s, 4216.546ms
- Vulkan8 (eight reported model layers): 30.271 tok/s, 4017.149ms

Under this bounded, longer generation workload Vulkan8 was
approximately 31% higher in observed median native decode and
approximately 23% lower in observed median HTTP latency than CPU.
All GPU requests had positive same-process/same-client AMD DRM
compute delta and loader-claimed exact layer placement. No tensor
kernel trace was independently signed.

Important: The CPU no-offload runtime still opened an AMD DRM
render client even with --device none --n-gpu-layers 0. Its GPU
compute counter was unavailable, so absence of GPU compute was NOT
independently demonstrated. Two pre-generation test scripts correctly
HOLD on unexpected/missing counter evidence before a third source
retained the missing counter as UNKNOWN. All three temporary CPU
attempts cleaned up completely.

Local raw receipts, failed pre-generation diagnostics, and launch
scripts are retained ONLY on x1-370 in research lab commit 3c6fdcd.
This PR adds the pure offline long_counterbalance_audit.py and
synthetic adversarial tests only, not host telemetry or real request
IDs. The assessor checks six receipts, exact mode/order/model/prompt
hash, 112 native tokens per request, positive GPU engine deltas,
precise loader offload counts, memory PSI, VRAM floor, and cleanup.
It always DENIES statistical superiority, production authorization,
independent tensor execution attestation, and independently proven
zero GPU compute in CPU mode.

This fixed two-block session is more relevant to decoding than
the prior 3-token microbench, but still NOT a statistical population
benchmark, multi-day stability claim or production route approval.
Full production HOLD, no PR merge/deploy, no secret reading or
trace privilege changes.

## November 8 seeded within-group randomized mixed-length Deathstar research

Single-host Deathstar RX480 LFM3 QAD-Q4_0 pinned checkpoint
SHA-256 43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452.
The source preregistered Python random seed 202611081321 BEFORE executing
the 12 primary stages. Four groups executed in fixed
short round1 / long round1 / long round2 / short round2 order, with
CPU/no-offload, Vulkan0 six layers, and Vulkan0 eight layers randomly
shuffled WITHIN each group. It was not fully randomized over day/session.

Each of 12 ephemeral loopback-only stages used the same pre-existing
llama.cpp binary, model alias, threads 4, ctx2048, sampling,
model checksum and port 19189. The short input was 124 characters/
38 prompt tokens with exactly 64 output tokens; long input 1456
characters/283 prompt tokens with exactly 160 output tokens.
All modes in each workload used identical exact request SHA-256.
Each block executed TWO distinct completions (24 primary total),
with TWO separately started processes per mode/workload cell.
Every stage passed source-side model hash, correct initial integer-list
response format, token counts, unchanged memory PSI and >=1.5GiB
free VRAM gates, positive source-reported same-process AMD GPU engine
delta for each GPU request, matching 6/25 or 8/25 Vulkan loader
offload records, and complete child/log/port cleanup.

Observed median native decode (tok/s) and median HTTP latency (ms):
    38 prompt / 64 completion tokens:
      CPU 23.767 / 3090.047, GPU6 30.802 / 2443.879,
      GPU8 32.299 / 2309.800
    283 prompt / 160 completion tokens:
      CPU 23.254 / 9690.087, GPU6 27.226 / 7054.395,
      GPU8 30.629 / 6498.854

GPU8 had highest observed native decode and lowest HTTP latency in
both of these workload conditions. Only FOUR responses per cell,
one session, fixed group order and independently UNSIGNED local
operator evidence; no statistical population claim, confidence
bound, independent kernel/tensor trace or production route authorization.

Three initial CPU prompt pilots were EXCLUDED from the randomized
results. The first pilot technically met token accounting but emitted
think-style text, not the requested integer list; the revised
source added a strict integer-prefix quality gate. The other two
pilots validated revised short/long tasks but were run before randomization.
All pilots cleaned up their own child/log/port.

The source verified only the initial numeric-output prefix plus
full response SHA-256; it did NOT independently validate every
integer past the prefix. Source-computed per-request DRM compute deltas
were not paired with externally signed fdinfo receipts; the CPU mode
GPU compute counter is UNKNOWN, not verified zero. No credential or
production-state access was made. All raw host JSONs, actual process IDs
and the launch scripts stay local in x1-370 research commit 936f1e1.

Pure random_mixed_audit.py checks fixed-seed preregistration,
mode and workload order, public request hashes, 12 stage / 24 response
counts, loader GPU depth, process/client continuity, memory/VRAM
gates, output prefix, exact token counts and cleanup. Synthetic
adversarial tests block forged results, replay, altered prompts,
unsafe pressure, misleading think output and gate weakening.
Research PASS ALWAYS sets independent_kernel_execution_attested=false,
cpu_zero_gpu_activity_verified=false,
significant_superiority_established=false,
production_authorized=false.

Keep PR #148 draft and unmerged. All production routes, deployment
permissions, signing and SGNC anti-rollback boundaries remain HOLD.

## November 8 exact-response quality gate: LFM3 fleet ops tasks

Deathstar RX480 LFM3-QAD-Q4_0 synthetic structured-output research:
12 preregistered blocks, 24 public synthetic inference requests,
CPU/no offload, Vulkan6 and Vulkan8; two tasks x two independent
stage starts per mode; fixed seed 202611081402 shuffled modes within
task groups. Same pinned full GGUF SHA-256
43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452.
All 24 model outputs used valid JSON and successful source-side
generation, but **ZERO of 24 exactly matched the required task
schema/content** when compared as fully parsed canonical JSON.

Task A, fictional release gates: correct HOLD decision was often
returned, but 0/12 output arrays had exact names and memberships;
some responses invented labels (e.g. PASS) or changed gate naming.
Task B, fictional node inventory: 0/12 exact responses because list
ordering violated the required sorted-array contract. Importantly,
all 12 node outputs DID have correct set memberships. We validated
this only after comparison to the COMPLETE synthetic source snapshot
and recovered correct sorted arrays via deterministic guard, not by
guessing or inventing data.

Research-only code:
- quality_task_audit.py checks the preregistration, pinned model
  and script SHA, true full public synthetic response digest, exact
  parsed JSON answer versus frozen oracle, token accounting, precise
  GPU layer-count/engine counters, host resource gates, unique PIDs
  and full ephemeral stage cleanup. A capture can PASS with zero
  exact-correct outputs, and NEVER claims the model useful merely
  because it generated tokens quickly.
- quality_canonical_guard.py has two pure, caller-source-grounded
  paths: node_summary_guard can sort model output only if EVERY node,
  status membership and total matches the complete caller-owned
  input snapshot (no missing, repeated, unknown or reassigned nodes).
  release_gate_guard computes a canonical gate decision directly
  from every required caller-owned gate state, marking model text
  advisory and refusing to fix fabricated labels or omitted gates.
  It rejects duplicate JSON keys, invalid statuses, NaN/non-JSON,
  wrong bool/int types and unsupported source shapes.

Both guards remain deploy-inert and ALWAYS return
production_authorized=false and source_independently_signed=false.
The underlying source snapshots were synthetic and not independently
signed release witnesses. Real protected Deathstar inference stage
and existing model servers were untouched. The CPU no-offload mode
still has unknown zero-GPU compute (DRM counter unavailable), and
per-request GPU metrics are local operator evidence rather than
signed kernel-specific dispatch.

The local lab retains all 24 synthetic host receipt JSONs and one
pre-registered-excluded CPU calibration, lab commit ffa57cd
(documented source experiment first committed at de3953a). The
GitHub research PR carries ONLY the pure checkers, 36 frozen
adversarial test cases, provenance and summaries. No raw live host
process IDs, timing receipts, full model answers, secrets or
credentials are published.

**Use-case gate:** exact model-produced structured output FAIL;
source-grounded node normalization PASS for these synthetic records;
model-derived release authority FAIL/HOLD; independently attested
GPU tensor execution HOLD; statistical generalization HOLD;
PRODUCTION ROUTING AND RELEASE HOLD.

## November 8 quality-adjusted research-only useful-output rates

Follow-up to issue #149 (exact LFM3 fleet-task JSON quality 0/24).
The same preregistered 24 local Deathstar synthetic fleet-task receipts
were re-audited OFFLINE. No additional GPU requests, production routes,
protected services or credentials were touched. Frozen source
snapshots were caller-controlled synthetic inputs without independent
signer custody. A complete source-verified node membership check
establishes that all 12 node-health responses can be deterministically
sorted. The 12 release-gate responses remain inaccurate/incomplete:
the canonical HOLD result comes directly from source gate records,
never from model assertions.

STRICT FULL-OBJECT MODEL SUCCESS PER REQUEST:
    CPU/no-offload: 0/8;
    Vulkan0 6/25 layers: 0/8;
    Vulkan0 8/25 layers: 0/8.
Thus STRICT MODEL-CORRECT completions per observed HTTP second
are zero for all three modes and both use cases.

SOURCE-VERIFIED AND DETERMINISTICALLY NORMALIZED NODES:
    CPU: 4/4; 12.941 summed model HTTP seconds => 0.309/s
    Vulkan6: 4/4; 8.966 summed HTTP seconds => 0.446/s
    Vulkan8: 4/4; 8.903 summed HTTP seconds => 0.449/s.
The denominator is sum of per-request model HTTP times ONLY; it
EXCLUDES process startup, source validation, normalization, external
network, queue, energy, real deployment success and contention.
These are optimistic research-only accounting ratios, NOT a
production throughput/SLO or statistically reliable hardware rank.
No verified model-produced gate completions exist; source-derived
HOLD does not count as a model completion.

Pure quality_adjusted_eval.py composes frozen quality_task_audit
and quality_canonical_guard, preserves the exact preregistration
and source snapshots and forbids promoting wrong-but-fast outputs.
The exact-JSON oracle is also hardened to reject duplicate JSON
keys, NaN/Infinity, nonobject types and bool/int confusion.
Native llama.cpp decode tok/s MUST correspond to its observed
(n-1) emitted token intervals / predicted_ms and fit the
observed HTTP elapsed duration. This rejects fabricated high
speed claims even when token accounting appears valid.

Source-only tests reject stale/fake counters, altered or absent
source gates, wrong node membership, changed request hashes,
malformed answer JSON, inconsistent native timing, pressure/VRAM
violations, missing receipts and model-manufactured quality passes.
All results always return production_authorized=false,
independent_gpu_kernel_attestation=false,
source_custody_independently_signed=false,
free_provider_billing_verified=false and
significant_performance_advantage_established=false.

The raw host-specific input receipts and final ledger remain local
to x1-370 lab commit 933ef5e. This PR publishes ONLY the pure
source/tests, synthetic fixtures, sanitized summary and pinned
vendor source SHA-256 fingerprints; NO raw host receipts or
user credentials are included. PRODUCTION ROUTING HOLD.

## Joyner Radeon AI PRO R9700 live read-only GPU capacity gate (Nov 8)

x1-370 Tailscale is logged out, so Joyner tailnet SSH was
unreachable. Joyner itself remains online in Tailscale. A trusted
existing Joyner known-host alias worked through an authorized LAN
destination override while StrictHostKeyChecking=yes remained on.
No credential, host-key or network configuration changed.

Live dGPU PCI 0000:03:00.0 has AMD vendor 0x1002, device 0x7551,
amdgpu driver and ~32GB dedicated VRAM. The existing protected,
loopback-only LFM3-QAD model process on port 19175 is healthy and
mapped to an AMD DRM client on this discrete GPU rather than iGPU.
Engine counters showed historical nonzero compute but no live
token-correlated GPU inference. No requests or API keys were used.

Resource capacity observed: VRAM ~32624MiB total/~32567MiB free,
GTT ~1135MiB free, system RAM ~1135MiB available out of ~15GiB
physical, swap ~0.047MiB free out of 4096MiB. At least five
model server processes already share this host. ROCm userland
CLI not installed in PATH; existing Vulkan runtime contexts work.

Additional conservative new-model research admission requirements:
>=10GiB system MemAvailable, >=1GiB free swap, >=1.5GiB
GPU VRAM, >=2GiB free GTT, PSI thresholds 1% memory/2% CPU/5% IO.
Current deficits: ~9105MiB available RAM, ~1024MiB swap,
~913MiB free GTT. No services were stopped or memory reclaimed.

Pure joyner_gpu_capacity_gate.py and its 16 synthetic adversarial
tests independently evaluate GPU PCI/driver/client/process continuity
and source pressure, but ALWAYS deny new model POST, protected
model generation, independent GPU token/kernel attestation and
production routing. Untrusted identity, host-key boundary,
credential access, incorrect hardware, invalid counters or
memory pressure fail closed.

Private live source probe and process data remain only in local
x1-370 research lab commit c6f867c. This PR contains just pure
source/test contract and a sanitized research summary. Production
paths, signing, ingress, provider selection and credentials
are unchanged.

## November 8 — retrospective two-path validation-inclusive replay

The new pure e2e_replay_comparator.py contrasts deterministic
source-only computation against independently source-verified
post-processing of historical model responses for the SAME frozen
synthetic six-node inventory and five-gate HOLD tasks.
Lab source: isolated x1-370 branch commit f65b02c (supplemental_source_commits
in VENDOR_PROVENANCE.json; original 15 sources remain pinned to prior
lab commit c6f867c). No live inference, protected service, keys,
prod router, provider route or deployment code touched.

Read-only study replayed the exact previously preregistered 24
Deathstar operator-local synthetic replies, then measured 128
repetitions per request per path after 8 warmup rounds, shuffling
each local replay round with a fixed seed (6144 LOCAL validation
timing samples). Median CPU-side Python source-only and guarded
model validation took microseconds on x1-370; the historical
Deathstar llama.cpp model HTTP calls took seconds each. These
two time types are from DIFFERENT hosts/runs. Adding them is a
reconstructed minimum proxy, explicitly NOT observed end-to-end
latency, production throughput, SLO or stable hardware ranking.

On the frozen full synthetic source, source-only computation
produced correct canonical output for all replayed 24 cases,
including correct HOLD directly from source gates. Across actual
historical models: 0/24 full strict JSON answers correct;
all 12 six-node model answers had correct complete membership but
unsorted arrays, and deterministic guarded normalization was
safe only after matching the entire source. All 12 model gate
answers were inaccurate or mislabeled. A correct deterministic
HOLD never counts as a successful model-generated release decision.

An incorrect model answer is retained as a zero-quality sample,
not confused with tampered evidence. A missing gate/source,
invalid preregistration, changed response hash, model authority
escalation, broken timer, unsupported sampling limit or unsafe
resource state FAILS CLOSED. Pure synthetic negative tests
exercise these distinctions. No raw host process IDs, full
experimental responses or unsigned execution receipts are
committed to this repository.

The evaluator ALWAYS returns:
production_authorized=false,
source_independently_attested=false,
kernel_execution_independently_attested=false,
full_end_to_end_latency_measured=false,
quality_eligible_for_production_routing=false.

Action: use deterministic source-only for these source-complete
tasks unless new representative task evidence demonstrates
incremental model utility. Draft research PR #148 remains
unmerged and production HOLD.

## November 8 — narrative-evidence LFM3 exact-output canary

A bounded, controlled Deathstar LFM3-QAD-Q4_0 experiment used six
FICTIONAL short incident narratives (TLS SNI, node authentication
contract, incomplete GPU tensor witness, missing TestFlight-linked
build, stopped database service, and insufficient evidence). The
model had to select a specific allowed issue category and a verified
evidence marker E1/E2/E3, using the last verified observation rather
than an earlier operator guess. The test text listed ALL six allowed
category codes and explicitly identified E3 as final verified
evidence; these prompts are easier than unguided real incidents.

One exploratory six-answer CPU block was excluded. Four freshly
preregistered blocks ran in fixed mirrored order CPU1/Vulkan8-1/
Vulkan8-2/CPU2; six distinct authored synthetic scenarios, repeated
twice per CPU/GPU mode, 24 primary requests total. Same fully
SHA256-pinned 1,049,615,680-byte QAD GGUF, same installed llama.cpp
binary, single localhost stage 127.0.0.1:19191, ctx2048, 4 CPU
threads, 1 slot, temp0, 96-token bounded JSON responses.
**Both modes achieved 12/12 FULL exact JSON category AND evidence
tags on the primary 24 requests.** The distinct scenario count is
SIX, not 24 independent held-out cases.

Measured IN THE SAME REQUEST SESSION on Deathstar:
* CPU (0 requested GPU layers): 3,275.465 ms median HTTP + safety
  checks + local response validation; 12/12 exact,
  0.303152 exact-correct replies per observed request second.
* RX480 Vulkan0 (8 reported offloaded model layers):
  1,683.979 ms median corresponding request path; 12/12 exact,
  0.601716 exact-correct replies per observed request second.
The measured interval excludes model startup, full GGUF hash,
external source custody, remote network, queue, energy and
real application dispatch. GPU model-layer loader counts and
source-local DRM compute deltas are not independent tensor-kernel
attestation. No proof CPU mode performed zero incidental GPU work.
This is one local session, not a robust hardware or accuracy
confidence interval and NOT production throughput/SLO.

An important ADVERSARIAL comparator was added AFTER seeing these
six notes: pure unstructured_rule_baseline.py implements simple
literal E3-keyword patterns and also matches all SIX seen cases
using approximately 8 microseconds median local classification.
This is post-hoc, NON-BLINDED and OVERFITTED to the authored
fixture: it is not an independently devised fair baseline and
must not be deployed as a general classifier. Because this simple
rule handles the same seen cases, the experiment **DOES NOT prove
unique or incremental LFM3 utility**. Both model and baseline
remain research-only and always deny production authority.

Vendor artifacts:
- Pure unstructured_triage_audit.py checks the frozen authored
  fixture SHA-256, source/preregistration digest, exact full
  response JSON oracle, 24 request IDs, process and GPU identity,
  pressure/VRAM resource gates and total child/log/port cleanup.
  Wrong model answers are quality failures, while malformed
  receipts, unsafe capacity or missing custody are HOLD.
- Pure unstructured_rule_baseline.py demonstrates a narrow
  post-hoc classifier that abstains on missing/ambiguous evidence,
  never promotes inferred release/production rights.
- One fictional public unstructured_public_fixture.json and
  synthetic adversarial test suites; NO raw host execution
  telemetry, host request IDs, model outputs or runtime scripts.
- Local-only original lab commits bd07a0c and c321779
  retain preregistration, six raw exploratory model responses,
  24 primary synthetic receipts and evidence SHA-256.
- source_lab_commit for the ORIGINAL 16 vendored files remains
  pinned at c6f867c. New three entries use
  supplemental_source_commits=c321779.

NEXT GATE: separately authored, prospective held-out narrative
cases; freeze a deterministic baseline BEFORE scoring. Include
ambiguous evidence and temporal contradictions. Require independent
scoring and custody before any model-assisted production admission.
All release/custody/billing/GPU-kernel gates remain HOLD. PR #148
remains draft/unmerged. No src/config/deploy code touched.
