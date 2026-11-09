# LFM3 structured decoding — offline request and response syntax preflight

**Research-only static protocol.** No network, model inference, remote process, shared fleet, production runtime, credentials or routing modifications. This DOES NOT establish live `llama-server` support, model accuracy, independent held-out source custody, or release authority.

## Why this separate contract exists

The original 36-case developer-authored LFM3 CPU/Vulkan trial showed **36/36 syntactically parseable two-key JSON objects but only 28/36 with strictly valid evidence-marker values** in both modes. The eight wrong-format replies often expanded the `evidence` value into explanatory prose rather than a single `E1`–`E9` marker. The frozen **whole-answer exact** result remains **19/36 vs 18/36** for the unchanged deterministic baseline; this is not independent or production-quality evidence. A separate, already executed prompt-v2 pilot had **6/12 exact per mode**, improved two known errors while regressing four previously correct known cases; it was **rejected**, not promoted as an improvement.

The proposed next DEV-only change is **schema-constrained decoding**, not another model instruction rewrite. The twelve targeted previously known development cases and all original prompts were fixed in a private local pre-execution manifest before any new request. The original model GGUF checkpoint remains `43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452`; the 96-token budget, temperature 0, local context settings, CPU/Vulkan resource gates and process cleanup script were intended to remain unchanged. The sampled cases are outcome-selected, so any future result would be **development debugging only, not an unbiased quality estimate**.

**Execution HOLD:** An attempted remote inference tool operation was blocked before running. No new CPU or Vulkan generation receipt exists, and there is no measured success or failure of structured decoding. Do not work around the block, and do not claim the runtime accepted the JSON Schema. The frozen but *unexecuted* local-only candidate is archived at `/home/scott/git/.lfm3-dev-cohort-20261108/dev_schema_constrained_block.py`, SHA-256 `34f5a7f5091798f096b9c48b761383576b1067e0552e3b47bfab7105b3ff0be6`, and an explicit local-only `HOLD_UNEXECUTED_TOOL_SAFETY_BLOCK` preflight manifest. Local archive Git has **zero remotes**; do not upload its raw notes, gold, or full host receipts.

## Exact documented chat response format

The current official llama.cpp server README documents `response_format` with `type=json_schema` and the schema **directly** under `response_format.schema` for its OpenAI-compatible chat endpoint: <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>. This is the one structure pinned by this proposal:

```json
{
  "response_format": {
    "type": "json_schema",
    "schema": {
      "type": "object",
      "properties": {
        "category": {
          "type": "string",
          "enum": [
            "beta_build_unlinked", "database_service_down", "gpu_attestation_missing",
            "insufficient_evidence", "node_auth_contract", "tls_sni_missing"
          ]
        },
        "evidence": {
          "anyOf": [
            {"type": "string", "enum": ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9"]},
            {"type": "null"}
          ]
        }
      },
      "required": ["category", "evidence"],
      "additionalProperties": false
    }
  }
}
```

The schema's exact canonical SHA-256 is `a11264970555ad2478b1d26ac458b37639da8bf18100e2da847a43395eb8739b`. A nested OpenAI-style `response_format.json_schema.schema`, or `json_object` without a schema, is **not accepted by this proposal's static checker**. This is a conservative contract choice, **not** a claim that alternate forms are universally unsupported by llama.cpp. The chosen schema subset itself has not been proven accepted by the installed research-server build; static source inspection is insufficient.

## Pure checks and boundaries

`structured_decode_preflight.inspect_request_shape` checks only an already formed, **fictional development** request: exactly one bounded user message with original fictional-prefix designation, exact local research model alias, temperature 0, max tokens 96, no streaming, disabled thinking, no tool/network/prod fields, and **byte-canonical equivalent** of the pinned direct JSON Schema. Canonical hashing avoids Python's subtle `False == 0` equality trap. The checker never echoes the prompt and never issues the request. `PASS_STATIC_DEVELOPMENT_SCHEMA_REQUEST_ONLY` means only that the request *shape* is ready for inspection, with `live_llama_server_support_verified=false` and `permitted_to_infer=false`.

`inspect_existing_reply` accepts only caller-supplied fictional notes/replies, reuses the strict parser rejecting duplicate JSON keys, non-finite values, extra fields, illegal classes and verbose markers, checks unique `E#` markers in the note, and allows `evidence:null` only for `insufficient_evidence`. It returns **`PASS_STATIC_REPLY_SYNTAX_ONLY`** without claiming the answer category is correct, supported by verified source facts, produced by a real model, or safe to act upon. The existing strict source-fact and model–rule agreement guards remain separate.

All results explicitly force `actual_model_execution_verified=false`, `independent_source_custody_verified=false`, `model_value_verified=false`, `permitted_to_infer=false`, and `production_authorized=false`. Unit tests are synthetic and do not require GPU, model weights, internet, external signers or any real incident data.

**Next acceptance boundary:** only a separately authorized bounded research execution, on a host passing the existing pressure/cleanup gates, may establish whether the exact pinned llama.cpp build actually honors this schema, and whether format validity improves without worsening classification accuracy. No such execution is currently authorized or evidenced. External held-out author/reviewer/witness and pre-inference sealed case corpus remain outstanding under issue #157. Do not merge, deploy or admit any production traffic from this static contract.
