# Real Lenovo LFM CPU cancellation through x1 Auto-Router

October 19, 2026. ISOLATED RESEARCH / NO DEPLOY / GLM AUTO-FAILOVER HOLD.

This continues local Auto-Router worker/owner/fence research beyond the
published draft PR #75. This newest work remains local, unmerged and
undeployed. No live model service, production route, database schema,
credentials, Tailnet policy or SSH configuration was modified.

## Exact model and server identity

- x1 K2-Horizon-1B-BF16 GGUF SHA256:
  371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8
- x1 LM Studio llama.cpp backend executable SHA256:
  699be04ebab1eab941867195f15ac1fbbd526b989071618a96e31c50bc14dadc
- Lenovo LFM2.5-1.2B-Instruct-Q8_0 GGUF SHA256:
  c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c
- Lenovo llama-server executable SHA256:
  56195f47a71aa24c1b89f07c14a510c7b1749cc8c519a0174a4116706a2eea50

These were measured by actual read-only sha256sum on each physical
model host. No model or binary was downloaded or modified.

## Physical isolation

Lenovo preflight confirmed about 8 GiB available RAM, zero memory-full
PSI, a healthy and active fleet-cpu.service, and free port 12742.
A *separate* temporary llama-server ran with loopback bind
127.0.0.1:12742, two low-priority CPU threads, one slot, no GPU layers,
1024 context and a 6-GiB virtual address limit. The live Lenovo model
listener on port 1234 was only queried for health.

x1 established an ephemeral, strict-host-key-pinned SSH local forward
from 127.0.0.1:12743 to Lenovo's temporary loopback listener. The
Lenovo canary source was transferred as in-memory Python command data:
no file or service was installed there. The helper required STOP or a
deadline, killed only its own temporary model process, and checked
that Lenovo's original fleet-cpu PID *starttime* and health remained
unchanged. The x1 driver refuses a successful outcome without
confirming that remote cleanup.

No test opened a public TCP port or invoked any live inference route.

## Physical results — two identical cross-host acceptance passes

1. Auto-Router's real OpenAICompatibleProvider.stream_completions on
   x1 requested a live synthetic counting completion from the isolated
   Lenovo LFM model. The genuine stream ran through the actual
   stream_lifecycle._dispatch_stream wrapper.
2. The strictly opt-in, *test-only* in-process authority approved
   chunks 1, 2 and 3, then rejected chunk 4. An independent Lenovo
   /slots witness showed it was still generating when chunk 4 was
   rejected.
3. The router released exactly three real chunks, suppressed the
   fourth, called exactly one abort hook, and classified the outcome
   possibly_accepted rather than completed.
4. The actual Lenovo serving slot became idle in 0.171s and 0.167s
   across two physical repeats (median 0.169s). Follow-up model
   generation succeeded on both repetitions.
5. Separate direct Lenovo HTTP stream-close test recovered its slot
   in 0.154s; a separate x1 K2-Horizon direct/adapter canary also
   passed. An initial Lenovo direct trial recovered in 0.307s but
   was not stored in the aggregate evidence.
6. All ephemeral Lenovo model processes, SSH tunnel and test ports
   were cleaned. Lenovo live fleet-cpu service remained healthy with
   the same process identity.

## Reproduction and evidence

Local opt-in research files:
- research/runtime_abort/qualify_llama_abort.py
- research/runtime_abort/lenovo_loopback_abort.py
- research/runtime_abort/lenovo_remote_canary_host.py
- research/runtime_abort/qualify_lenovo_router_stream.py
- tests/test_runtime_abort_canary_research.py
- tests/test_lenovo_real_abort_canary_research.py

Private JSON evidence under research/runtime_abort/evidence:
- x1_real_1b_abort_revalidation_20261019.json
- lenovo_lfm_real_stream_abort_20261019.json
- x1_router_lenovo_real_fence_20261019.json
- x1_router_lenovo_real_fence_revalidation_20261019.json
- crosshost_lenovo_real_cancellation_aggregate_20261019.json

Evidence contains only model identifiers/hashes, activity counters and
timings, no client prompts, user responses, API keys or secrets.

The x1 runner is PLAN_ONLY unless explicitly invoked with --run using
the existing Auto-Router virtualenv. It checks correct host, SSH peer,
ports and startup/cleanup. Isolated K2 runner now checks Python router
dependencies BEFORE launching its CPU model; a system Python missing
pydantic_settings had caused an earlier environment-only failure.

## Limits / release HOLD

**The owner/epoch authority was synthetic.** No independently
authenticated humans, trusted AssistX claim issuer, signed PostgreSQL
epoch/high-water, real worker revocation, durable trusted terminal
signer or quorum-based cross-host primary takeover were involved in
this real Lenovo test.

HTTP response closure and release of a llama-server inference slot
are not independent proof that all model threads, GPU kernels or tool
side effects have stopped. Buffered bytes accepted before revocation
could still reach a client after that event. The test protects the
router's acceptance/yield boundary, not end-to-end client delivery.

Lenovo's live fleet-cpu.service currently listens on 0.0.0.0:1234.
The listener's authentication, Tailnet/LAN ACL and least privilege
must be audited before any production routing admission; do not
silently change it as part of this canary.

The small Lenovo CPU model does not have equivalent capability to
Joyner's planned GLM-5.3 321B service. Automatic failover still needs
an explicitly reduced-capability task subset, trusted two-person
access, real signed owner/epoch admission and revocation propagation,
verified model abort, independent anti-rollback receipt custody,
controlled rollback, negative fault injection and a 72-hour soak.

Decision: RESEARCH PASS, PRODUCTION HOLD.

## Regression result after physical Lenovo integration

Dedicated Lenovo/K2 safety tests: 29 passed.
Full Auto-Router nonintegration suite on this isolated research
checkout: **582 passed, 9 skipped, 1 Starlette/httpx deprecation
warning; one subtest passed**. The live served-model registry drift
check that was previously intermittent did not fail in this particular
run, but one green runtime sample does not establish catalog stability.
No acceptance gate was disabled to obtain a green run.
