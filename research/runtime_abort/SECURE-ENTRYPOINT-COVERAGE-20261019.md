# Secure inference entrypoint coverage — October 19, 2026

**Review-only source audit. No service deployment or runtime configuration change.**

## Existing physical evidence (prior committed branch)

This branch descends from local Auto-Router research commit
`6b9efc9603f6bc2f68823b41008cffc77cbf2839`.
Two x1-to-Lenovo real llama-server stream abort trials had already
passed: the synthetic test authority rejected the fourth real model
stream chunk, Auto-Router suppressed that chunk, the isolated Lenovo
slot returned idle (0.171s and 0.167s), and follow-up generation
succeeded. The live Lenovo fleet-cpu service remained unchanged.
See REAL-LENOVO-CROSSHOST-CANCELLATION-20261019.md.
Authority in those trials was **synthetic**, not an independently
authenticated PostgreSQL epoch broker.

## New production-entrypoint audit

The observed running router process on x1 invoked
`uvicorn auto_router.secure_live:app --host 0.0.0.0 --port 8088`.
The proposed code branch is NOT this live process's deployed code.

Read-only source inspection confirms the actual secure entrypoint's
installation order:

1. install_request_idempotency(app, state, router_module)
2. install_stream_lifecycle(router_module)
3. install_executor_inference_auth(app, state)

`main_live.py` alone does not install the lifecycle patch; the
deployed `secure_live.py` wrapper does. This resolves a possible
source-level confusion: direct import of main_live is NOT identical
to the running secure_live entrypoint.

New regression file:
`tests/test_secure_entrypoint_effect_coverage.py`.

It asserts:
- Every recognized POST /v1 inference route in main.py is protected
  by the executor JWT middleware:
  /v1/chat/completions, /v1/responses, /v1/completions,
  /v1/embeddings.
- The secure entrypoint installs its idempotency, stream lifecycle
  and executor authentication guards in the intended order.
- The strict-offline main_live compatibility layer patches the
  underlying main module's dispatch functions. Retired in-process
  /jobs/agent and tool-capable AssistX paths remain separated from
  local model inference authority.
- Strict effect mode refuses *non-streaming* calls in all four
  inference route classes, so embeddings or stream=false cannot
  bypass the per-chunk gate.
- Strict effect mode refuses missing internal trusted authority for
  chat/completions, responses and completions *before* provider
  dispatch.
- User request JSON or metadata cannot inject the authority.

Results: **34 targeted tests passed**, including the existing stream
effect suite. The complete Auto-Router non-integration test run on
this isolated checkout recorded **594 passed, 9 skipped, 1
Starlette/httpx deprecation warning**, plus one passing subtest.
The intermittent x1 model-registry drift did not occur in that run;
a green single sample is not proof of stable live provider inventory.

## Important limits and next release gates

- These source-level guard coverage checks avoid importing secure_live
  as a local ASGI app, because its production runtime requires an
  inaccessible local `/data` configuration mount. No claims of live
  authenticated endpoint acceptance are made.
- Directly installing/relaunching main_live instead of secure_live
  must be prohibited by an operator-reviewed deployment manifest.
- The combined real Lenovo generation + independently sourced
  PostgreSQL epoch-revocation harness was NOT executed. An attempt
  to create it was blocked by an OpenAI safety check; we did not
  bypass that check.
- The new stream effect gate is DEFAULT OFF in the local research
  branch and no trusted production `state.stream_effect_authority`
  exists. Turning it on would intentionally DENY inference.
- Other mounted routes and cross-service direct model listeners
  require a separate integration/security audit, not only the
  four /v1 paths. The Lenovo production listener was previously
  observed at 0.0.0.0:1234; Tailnet/LAN ACLs and service-side
  authentication must be reviewed before admission.
- The real model canary demonstrates HTTP-stream closure and slot
  recovery. It does NOT attest internal model cancellation, prevent
  previously buffered bytes arriving after revocation, or prove
  independent signing/key custody, two authenticated users,
  rollback-resistant epochs or replica failover.
- Small Lenovo LFM2.5 is not equivalent to GLM-5.3; automatic
  fallback must have an accepted limited capability policy.
- A production-like 72-hour soak, operator handoff, alerting and
  rollback still remain open.

**Decision: SOURCE/TEST PASS; PROD ROUTING AND AUTO FAILOVER HOLD.**
