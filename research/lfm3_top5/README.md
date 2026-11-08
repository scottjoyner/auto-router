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
