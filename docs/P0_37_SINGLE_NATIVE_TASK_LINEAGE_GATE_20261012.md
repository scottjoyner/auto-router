# P0 #37 — Single Native-Task Canary Shape and Prism Allocation Lineage

**October 12, 2026 (EDT) · Status: RESEARCH / DRAFT / NEVER DEPLOYED.**  
**Parent:** [native two-signature join draft #63](https://github.com/scottjoyner/auto-router/pull/63), stacked on physical preflight [#62](https://github.com/scottjoyner/auto-router/pull/62), signed observer [#60](https://github.com/scottjoyner/auto-router/pull/60) and shared-owner admission [#51](https://github.com/scottjoyner/auto-router/pull/51).  
**Blocker issues:** [P0 human trace #37](https://github.com/scottjoyner/auto-router/issues/37), [physical node #61](https://github.com/scottjoyner/auto-router/issues/61).  
**Native code reference:** [source-exact Prism branch](https://github.com/scottjoyner/llama.cpp/tree/experiment/p0-prism-task-allocation-lineage-20261012), stacked on the [uninstalled native lifecycle tap branch](https://github.com/scottjoyner/llama.cpp/tree/experiment/p0-prism-2459f68b-native-tap-20261011).

## Finding: one HTTP request does not imply one native task

Read-only examination of the exact source tree associated with xwing's
`llama-server` showed that in
`server_routes::handle_completions_impl`, a
`server_task.id` is minted by `rd.get_new_id()` **for each
parsed prompt input**. Further, if the resolved
`task.params.n_cmpl > 1`, the code creates child task IDs using
`task.add_child(parent.id, rd.get_new_id())`.

The previous three native task lifecycle hooks
(`slot_assigned`, `sampled_token`,
`final_result_enqueued`) alone could not show *how many*
tasks a request allocated, nor whether a sampled task was a child
of a parent request. A future P0 receipt that silently treats one
native integer task ID as a complete multi-prompt/multi-output
request would be misleading.

The new **unmerged / branch-only** Prism experiment adds:

- `task_allocated` (stage 0) after resolving the task's schema and
  creating any child IDs, before passing tasks to `rd.post_tasks`.
  The event carries native task ID and `slot_id=-1` because no
  inference slot has accepted it.
- A separate `task_allocated` event for each `n_cmpl > 1` child
  with `parent_task_id` set to its actual parent.
- Explicit parent ID on later slot, token and final-result events
  (or `-1` for top-level tasks).
- Strict C++17 fixture checks for top-level parent/child ID fidelity,
  invalid task and slot IDs, no self-parenting and no observer
  registration or active sink in the **normal/default-OFF** build.

The source-exact lineage branch derives from the actual observed
Prism source Git commit
`2459f68b5c0eb26261fd5a81682004b93cd645ba`,
but **this does not attest the running xwing binary's build
provenance**. The xwing process was NOT modified, restarted,
authed as a person, or sent a generation request. These hooks
are compile-time **NO-OP** without the special fixture-only macro.
They do not sign, log or send network traffic.

**Most important semantics:** `task_allocated` occurs before
`rd.post_tasks`. It says a C++ task integer was allocated,
**NOT** that queue posting succeeded, the task reached a slot,
the model ran, or a response was delivered. Client identity is
NOT available from a trusted native root/attempt claim here;
this new stage is not an ingress-auth implementation.

## Narrow Auto-router canary request qualification

`src/auto_router/p0_single_task_shape.py` is a pure, **uninstalled**
predicate taking the router's already verified signed-v2
ASGI actor and original request body before service-metadata
injection. It independently checks the exact original v2
canonical body SHA256 and bound model.

It accepts ONLY:

```json
{
  "model": "auto/code",
  "messages": [{"role": "user", "content": "short test text"}],
  "max_tokens": 8,
  "stream": false
}
```

`stream` is optional and either actual JSON boolean is allowed.
The single plain-text message is capped at 512 characters; the
UTF-8 canonical JSON at 2,048 bytes; tokens at 1..16.
Only those four exact keys are allowed.

Even **correctly signed** `n=1`, `n=2`, batch prompts,
multi-message prompts, `best_of`, `tools`,
`parallel_tool_calls`, multimodal content, extra model
metadata, client `user`, `id_slot`, unsupported sampling
settings or unknown fields **fail closed**, rather than
guessing their native task counts.

This is deliberately stricter than the generic OpenAI-compatible
chat API, and its output explicitly remains:

```json
{
  "shape": "single_plain_text_chat_candidate",
  "expected_native_task_count": 1,
  "actual_native_task_count_observed": null,
  "native_parent_child_lineage_proven": false,
  "native_execution_attested": false,
  "physical_inference_permitted": false,
  "operator_approved": false,
  "response_delivery": "unknown"
}
```

There is **no** installed middleware, production admission flag,
model call, or release promotion path. A request passing the
gate **does not prove** the server actually allocated just one
native task: compiled server version, configuration defaults,
chat-template behavior and real task-allocation instrumentation
must all be independently verified. The signed ASGI actor's
source must be the trusted v2 middleware; copying actor-shaped
client JSON is not authentication.

## Exact test and physical acceptance matrix

| Boundary | What is presently proven | What is NOT proven |
| --- | --- | --- |
| Human gateway actor | Signed-v2 fixture actor passes real ASGI auth; modified prompt denied | Real independent IdP Alice/Bob |
| P0 request shape | Strict original-body digest, one user message, <=16 tokens, no batch/tool/multimodal | Actual native task count under deployed server |
| Native task lineage | Two C++17 fixture builds PASS: default OFF and fixture ON; actual source-stage/parent lineage checked | Complete Prism full-server compile, deployed hook, installed observer, signed root-to-task binding |
| Native process | xwing targeted strict-SSH socket/PID/boot/start/inode and GGUF mmap previously observed | Cryptographic binary/model hash, native authenticated resident instance or real request |
| Signed native receipt | [Draft Auto-router #63](https://github.com/scottjoyner/auto-router/pull/63): dual-signed *synthetic* ingress/task fixtures pass; real ASGI-to-fixture Alice/Bob deny tests | Operational node signer, replay ledger, independent per-attempt process observations |
| Storage and delivery | No production writes; Beelink prior strict pressure **NO-GO** | Approved encrypted tenant/subject/producer/node trace custody, iPhone delivery |
| Failover and canary | Hosted disposable PostgreSQL owner tests PASS | Independently fenced production primary, sustained xwing headroom, operator-approved model generation |

Run the router test gate on an isolated checkout:

```sh
ruff check src/auto_router/p0_single_task_shape.py \
  tests/test_p0_37_single_task_shape.py
python -m pytest -q --disable-warnings tests/test_p0_37_single_task_shape.py
python -m pytest -q --disable-warnings -k 'not test_registry_matches_live_hosts'
```

At source SHA `4d87d1cfb5caf4a42b0f9c5be085271d946f255d`
on x1-370: **50 targeted fixture tests PASS** and
**743 wider isolated tests PASS / 27 SKIP / 1 known
live-registry-drift check deliberately deselected**, Ruff PASS.
These are **not** full unfiltered integration/physical CI:
optional PostgreSQL/AssistX cross-repo tests were skipped in this
isolation context. A separate hosted Linux fixture workflow
will independently validate the focused gate.

Native branch standalone strict C++17 default-OFF and fixture-ON
executables both PASS at
`b0503a585ce3a65d8a3ead35255f60b9507d2a4e`,
with a static check confirming **five** observation call sites
(two allocation paths plus three existing lifecycle hooks).
The toolchain did NOT build or restart xwing's physical
`llama-server`; server-wide CMake and throughput tests remain open.

## Next non-overlapping gates

1. Obtain a **trusted post-auth claim** inside the native HTTP
   boundary and bind one verified signed root/attempt/body digest
   to **all** actual native task IDs generated after parsing,
   including fanout, through a signed admission nonce. The current
   native hook never reads or trusts caller-provided `user`,
   `traceparent` or arbitrary `X-Request-ID`.
2. In a disposable native server build, prove task allocation count
   vs. signed admission, then slot/token/final/error/cancel paths.
   Reject missing, duplicated, swapped, or unobserved children;
   verify loss/backpressure and interrupts without fabricating
   execution.
3. Separately establish independently controlled ingress and
   observer signer credentials, real process/build/model digest,
   bounded nonblocking telemetry, durable fenced anti-replay,
   encrypted actor-scoped trace custody and ACL-filtered readback.
4. Only after real two-person IdP approval and sustained safe
   host/model residency may an **explicitly approved**, narrowly
   bounded physical A/B canary be attempted, followed by
   actual device/client delivery proof.

**Keep everything draft and unmerged. Do not autoload models, reuse
protected Bonsai API keys, restart the running xwing server, send
inference traffic or alter pressured Beelink/NAS storage on this
basis.**


## Composed verification boundary: shape BEFORE signed native bundle

An additional, **uninstalled** helper
`src/auto_router/p0_single_task_canary_join.py` now composes
`qualify_strict_single_native_task_shape` with the existing
two-independent-Ed25519-signer receipt verifier from
[draft #63](https://github.com/scottjoyner/auto-router/pull/63).
Its execution ordering is deliberately strict:

1. Verify that the actor is already the trusted signed-v2 ASGI
   claim, and independently recheck the **original request body's**
   signed digest plus narrowly allowed single-message shape;
2. Only if the shape qualifies, verify the separate post-auth
   ingress-signed native task receipt and separately signed native
   lifecycle events;
3. Return only *research correlation claims* and explicitly preserve
   `actual_native_task_count_observed=null`,
   `native_allocation_complete=false`,
   `native_parent_child_lineage_proven=false`,
   `cross_bundle_replay_verified=false`,
   `provider_execution_attested=false`,
   `physical_inference_permitted=false`, and
   `response_delivered=unknown`.

The integration test monkeypatches the native-join function with a
sentinel for bad **but correctly signed** multi-task shapes. The
sentinel must never execute: the request-shape boundary rejects
`n`, prompt arrays, multi-message prompts, tool calls and
caller metadata **before** checking any native signature. It also
checks that Bob or a new retry attempt cannot use Alice's signed
native-task bundle.

**Adversarial incompleteness demonstration:** two independently
signed, *internally consistent* synthetic native-task receipts can
refer to **two different native task IDs under the same signed
root/attempt**. The stateless verifier can correlate *both* in
separate calls. Thus a valid signed bundle **does not prove that no
additional native tasks were allocated**. Until an independently
witnessed, durable, fenced *complete allocation set* exists, neither
proof may state that all native work was observed.

This is not a deployed native ingress signer or collector; fixture
private signing keys exist ONLY in pytest. No C++ hook receives or
verifies real signed gateway roots or attempts.

### Latest exact results (October 12 EDT)

At Auto-router source and workflow SHA
`1c5808ddaf2f22ecdb776167e6b67938e341773d`:
- x1-370 Ruff **PASS**; **67 focused signed-shape-plus-join tests PASS**;
  broader isolated regression **760 passed, 27 skipped, 1 existing
  live-registry-drift test deselected**.
- [Focused hosted canary gate run 38230011613](https://github.com/scottjoyner/auto-router/actions/runs/38230011613)
  **SUCCESS: 67 tests + Ruff** (all hosted fixture tests; no model).
- [Inherited hosted PostgreSQL ownership run 38230011587](https://github.com/scottjoyner/auto-router/actions/runs/38230011587)
  **SUCCESS: 81 tests + Ruff** against disposable PostgreSQL 17.
  Deliberate negative permissions/bootstrap subtests emit PG error
  text into CI logs while the assertions correctly pass.
- The exact-source Prism C++ lineage experiment on x1-370 at
  `b0503a585ce3a65d8a3ead35255f60b9507d2a4e` had
  **two strict C++17 builds/executables PASS** (normal-OFF and
  fixture-only ON), and a source check verified **exactly five**
  inert emission call sites. No full Prism server binary
  build was claimed or triggered.

**Potential false-positive boundaries deliberately held open:**
the signed single-chat JSON request is *only a shape candidate*;
the native C++ `task_allocated` event occurs before queue posting;
the native process is not executing signed real-user requests under
the experimental code; no operational ingress signer/observer exists;
no full GGUF or running-binary cryptographic attestation; no complete
native task allocation-set receipt; no durable cross-bundle replay
fencing; no real IdP, encrypted subject-scoped custody, safe-canary
approval or device delivery. **P0 #37/#61 remain NO-GO.**
