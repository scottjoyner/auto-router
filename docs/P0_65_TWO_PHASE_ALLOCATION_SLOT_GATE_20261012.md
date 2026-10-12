# P0 #65: Corrected Two-Phase Native Execution-Evidence Contract

**Checkpoint:** October 12, 2026 (EDT)  
**Status:** **DRAFT / fixture-only verification / NO-GO for physical execution or rollout.**  
**Tracking:** [Native timing/auth blocker #65](https://github.com/scottjoyner/auto-router/issues/65), [physical witness #61](https://github.com/scottjoyner/auto-router/issues/61), [two-human task #37](https://github.com/scottjoyner/auto-router/issues/37).  
**Stack:** [strict signed-shape PR #64](https://github.com/scottjoyner/auto-router/pull/64) → [dual-signed fixture PR #63](https://github.com/scottjoyner/auto-router/pull/63) → [read-only physical preflight #62](https://github.com/scottjoyner/auto-router/pull/62).  
**Companion exact-source Prism branch (NO active deployment):** [`experiment/p0-prism-task-allocation-lineage-20261012`](https://github.com/scottjoyner/llama.cpp/tree/experiment/p0-prism-task-allocation-lineage-20261012).

## Why the original v1 experiment cannot become an operational ingress signer

The Prism server route **allocates** native `server_task.id` while preparing
a completion request; only later can `launch_slot_with_task` identify the
actual slot. An incoming HTTP signature cannot truthfully sign a measured
slot ID that doesn't exist yet. In addition, native translated request JSON
may not equal the original gateway-signed request, and header multimap
collapsing can hide repeated or conflicting authentication headers.

This revision **does not modify v1**, whose synthetic tests remain useful
for testing signed-claim correlations. Instead, a separate
`src/auto_router/p0_two_phase_native_evidence.py` establishes a
phase-correct, verify-only *v2 experimental protocol*.

### Phase A — post-auth *allocated-task-set claim*, NO slot identity

A hypothetical protected native ingress signer asserts a single bounded
allocation set **after** native task IDs have been allocated, **before**
queue posting and slot admission. Its Ed25519-signed, canonical JSON
payload is domain separated with `p0-native-allocation-set-v2\0`.

The exact fields bind:

- Human issuer/tenant/subject and original verified v2
  `root_request_id`, `attempt_id`, endpoint, original canonical
  request SHA256 and approved model handle;
- **Actual forwarded HTTP method/path**, **exact forwarded byte-count
  and SHA256** (not reserialized or reconstructed from the original
  request);
- Operator-pinned node/runtime/boot UUID/PID/process-start ticks and
  a *claimed* GGUF SHA256 digest;
- Unique admission nonce, bounded timestamp and an array of
  `{native_task_id, parent_task_id, allocation_index}`;
- **No** `slot_id` and **no** claim that the queue accepted the task,
  the model processed it or a network client received a response.

For this P0 gate, only a **one-entry allocation set with parent ID -1**
is allowed, after an independent strict single-chat shape check.
Even a correctly signed `n=1` explicit option, `n>1`, multiple prompts,
multimodal input, tool calls or caller-provided `user` metadata is
denied before any native signed evidence is parsed.

### Phase B — separately signed native *slot claim*

A second, distinct Ed25519 key signs domain-separated
`p0-native-slot-observation-v2\0` records, referencing the
**SHA256 of the entire original Phase-A signed payload plus signature**
under an independent reference domain. This record adds **the actual
slot ID only after allocation** and rechecks exactly the claimed
node/runtime/boot/PID/start/model identity, native task ID, parent ID,
UUID event ID, strict sequence and bounded timestamp.

An observer with operational credentials **does not exist yet**.
The signing keys in test fixtures are generated on the fly and
discarded, and two distinct public keys do not establish independently
governed *real* signer custody or source truth.

### Raw HTTP header parsing — guard *before* map conversion

The Prism source has an HTTP multimap of raw headers. Its
`get_headers` helper copies to `std::map` using
`headers[key] = value`, hiding repeated keys. Future native admission
verification must occur *before* that narrowing step and deny
duplicates, mixed-case variants of protected headers and simultaneous
`Authorization` plus `X-Api-Key`. Control bytes, oversized values and
malformed header-name tokens are also denied.

This is tested two ways, neither deployed:

- Python `verify_raw_native_auth_header_shape` accepts the original
  ordered raw pairs and returns only a **shape-only** report with
  `authenticated_forwarder=false`,
  `authenticated_human=false`, and
  `signed_native_admission_verified=false`.
- Standalone strict C++17
  [`server-p0-raw-header-shape.h` and smoke fixture](https://github.com/scottjoyner/llama.cpp/tree/experiment/p0-prism-task-allocation-lineage-20261012/tools/server)
  repeat the duplicate and last-value-wins map experiment, with
  control-character and size/budget negative tests. This header is
  **NOT included by the actual Prism HTTP handler** and is
  **NOT an activated authentication rule**.

A unique header is not cryptographically verified just because
its frame is well formed. The protected sender must be authenticated
independently, with strict signature/issuer/audience/key custody and
raw wire-body matching. A shared service bearer cannot establish
the end user's identity.

## Machine-readable result is deliberately DENY-ONLY

For valid separately signed *synthetic* Phase A and Phase B records,
the highest permitted verdict includes:

```json
{
  "signed_allocation_set": "synthetic_allocation_claim_correlated",
  "signed_slot_claims": "synthetic_slot_claims_correlated",
  "expected_native_task_count": 1,
  "claimed_allocation_task_count": 1,
  "claimed_slot_count": 1,
  "all_signed_allocations_have_slot_claims": true,
  "actual_native_task_count_observed": null,
  "native_allocation_complete": false,
  "operational_ingress_signer_proven": false,
  "operational_slot_observer_proven": false,
  "durable_replay_fencing_proven": false,
  "model_artifact_sha256_measured": false,
  "provider_execution_attested": false,
  "physical_inference_permitted": false,
  "operator_approved": false,
  "response_delivered": "unknown"
}
```

`all_signed_allocations_have_slot_claims=true` means **only** that
the signed task-ID set and the signed *claimed* slot records match.
It does **not** assert the signer truthfully enumerated every task.
The stateless verifier can accept the same receipt twice; durable
fenced single-primary replay prevention is not implemented. Neither
fixture signer is an independently attested native process sensor.

The verdict excludes raw prompts, wire body, header values, tenant and
subject to avoid leaking sensitive data.

## Test scope and cross-repository provenance

Run under an isolated auto-router development checkout:

```sh
ruff check src/auto_router/p0_two_phase_native_evidence.py \
  tests/test_p0_37_two_phase_native_evidence.py
python -m pytest -q --disable-warnings \
  tests/test_p0_37_two_phase_native_evidence.py
python -m pytest -q --disable-warnings \
  -k 'not test_registry_matches_live_hosts'
```

At tested code/docs SHA `913d1200132a4e512ff00d2b74aa74c54b24caf2`
on x1-370, Ruff **PASS**, **91 focused adversarial tests PASS**,
and the wider isolated suite **851 PASS, 27 SKIP, one known
live-registry-drift test deliberately deselected**. The focused
fixture suite includes **actual signed-v2 ASGI middleware**
Alice/Bob tests and signed-generic-batch-then-strict-P0-denial.
This does not run full unfiltered PostgreSQL/AssistX integrations.

Hosted exact-head evidence at the tested code SHA:
- [P0 two-phase native allocation and slot contract — run 38233503361](https://github.com/scottjoyner/auto-router/actions/runs/38233503361):
  **SUCCESS**, Ruff **PASS**, **91 tests PASS** with ephemeral keys.
- [P0 shared-owner disposable PostgreSQL — run 38233503167](https://github.com/scottjoyner/auto-router/actions/runs/38233503167):
  **SUCCESS**, Ruff **PASS**, **81 tests PASS**.

C++ branch (header guard and native allocation lineage) remains
**branch-only**, at
`8b264bb02d1ef0e1bcc8cabc51b658c332680bee`.
The three standalone strict C++17 fixture executables (raw-header
negative guard, normal native instrumentation OFF and native
fixture ON) all **PASS**; the normal CMake build does NOT define
the native hook macro, and `server-http.cpp` does NOT include
or activate the raw-header guard. It is **NOT a full server build**.
There has been **NO** full server CMake build, runtime deployment,
model inference, API credential extraction, xwing restart, or
Beelink/NAS modification.

## Remaining distinct production and physical gates

1. Protected raw HTTP signature verification **before** lossy map
   conversion; trusted gateway-to-server forwarding actor, exact
   original+native-wire bytes and per-request model/endpoint claims.
2. Actual native task allocation set signed by a nonblocking ingress
   producer **after allocation but before posting**; separately proven
   queue acceptance/slot admission and complete parent/child set.
3. Real independent native process observer with protected private
   signing-key custody, measured source/build and full model artifact
   digest, error/cancel/token/final events and durable restart-proof
   replay/anti-split-brain fencing.
4. Encrypted producer/node/tenant/subject trace custody, actor-scoped
   readback ACL and revocation, actual two-person IdP sessions and
   physical-device/client delivery receipts.
5. Sustained physical host-pressure and native model residency approval,
   explicit bounded operator canary authorization and final release
   evidence. Beelink is still NO-GO under storage pressure, and the
   protected xwing Bonsai model was not exercised by this work.

**Remain DRAFT, unmerged, uninstalled and NO-GO.**
