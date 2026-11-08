# LFM3 full-cohort session stability (secondary research gate)

This intentionally **does not change** the frozen prospective rule, scorer or preregistration of draft PR #152. It is a **secondary descriptive audit**; do not interpret a synthetic contract PASS as incremental model value.

## Capture plan

- Obtain a separately authored, adjudicated, preregistered and externally sealed held-out narrative corpus before producing any model outputs. Confirm that CPU and Vulkan sessions use the identical frozen checkpoint, prompt, binary parameters and note identities. The already-frozen primary case score remains one vote per unique case.
- Run the complete cohort twice per mode, with four unique stage/session identities ordered CPU -> Vulkan8 -> Vulkan8 -> CPU, stage boundaries and cleanup. Each session must include **every case exactly once**, creating 4N raw requests for N distinct cases. Do not count those 4N requests as independent test cases.
- Capture full raw two-key JSON replies and SHA-256 per response; unique request IDs, matching note hashes, finish reasons, per-request HTTP + local validation times, source checkpoint and claimed capacity/cleanup receipts. Keep receipts restricted to approved local storage.
- Fail closed on missing/duplicated observations, session ID reuse, stage order errors, malformed latency, source hashes, response tampering, CPU/GPU layer claim contradictions, or missing cleanup/capacity claims. Claims alone are not independent attestation.
- Report per mode: first/second correct, correct in both sessions, case-level correctness flips, identical raw reply count, and exact unique-case denominator. No p-values or generalization claims from repeat attempts alone.
- Require separate external custody, provenance review, protected service authorization, resource headroom, real kernel-execution witness, independent evaluation and production release decision. No rollout, credential use, merge or provider-route change is granted by this audit.

The synthetic unit fixtures are invented, not held-out incidents. This tool always leaves **independent_repeatability_verified**, **model_value_verified**, **external_custody_verified**, **accelerator_attested**, and **production_authorized** false.
