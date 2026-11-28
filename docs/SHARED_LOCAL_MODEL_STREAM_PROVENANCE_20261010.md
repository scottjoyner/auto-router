# Router streamed-inference provenance: configured vs attested

2026-10-10. Draft source-only hardening stacked on auto-router PR #35.

## Defect
Before this patch the shared stream outbox copied runtime_instance_id,
model_instance_id, runtime_projection_generation and assistx_executor.claim_id
from request metadata. Some metadata may originate in client-supplied JSON,
so an operator could mistake a spoofed GPU/model/claim for proven execution.

## Security change
- Node, runtime and model-instance selectors in the stream outbox come only
  from the router-selected ProviderCandidate config, with bounded safe IDs.
  The source label is configured_route_not_runtime_attestation: config is
  NOT proof of an actual executing model process.
- Unverified projection-generation and claim references are not carried
  from client metadata to the shared trace. authenticated_claim_binding
  explicitly remains false pending an independently verified server receipt.
- Upstream exception text is never included in shared outbox metadata
  because it may contain response bodies, URLs, credentials or prompts.
  Error type and fixed terminal-evidence enums remain.
- This patch does not change routing admission, provider selection, quota
  policy, authentication or any live inference endpoint.

## Existing safeguards
Production uses auto_router.secure_live with stream lifecycle accounting.
The parent PR #35 adds genuine provider terminal proof: OpenAI SSE [DONE],
Responses terminal events or explicitly incomplete/failed/cancelled states.
HTTP 200 alone does not prove successful generation.

## Tests / remaining gates
Adversarial tests inject fake node/model/runtime/claim IDs and a secret-like
exception string. Unit tests ensure none become shared trace evidence.
No raw prompt is stored. No production server, dataset or model changed.

Pending: server-attested tenant and per-request user identity; physical
LM Studio and llama.cpp instance proof; bounded span custody; encrypted
offline replay; two logged-in users concurrently sharing one endpoint.
