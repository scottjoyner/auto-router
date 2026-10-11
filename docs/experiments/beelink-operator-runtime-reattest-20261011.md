# Operator-approved Beelink runtime reattestation — 2026-10-11

**Status: ACTIVE as of the post-apply check, TIME-LIMITED.** Historical operator review record only; do not treat this document or an HTTP response as a signed host-side execution receipt.

## Approval scope

The user explicitly approved initiating the previously documented runtime reattestation and offered a GPU lease if needed. No GPU lease was requested or reserved; the already-running Beelink LM Studio llama-server model was sufficient. No new model was loaded or downloaded.

Physical observation via Fleet Commander:
- Beelink Ryzen 7 Mini PC, existing LM Studio-managed llama.cpp/Vulkan runtime.
- Exactly one host-reported loaded language model: liquid/lfm2.5-1.2b, Q8_0, **8,192 actually loaded context**. Other ten /v1/models inventory entries were not loaded.
- Running model-serving PID confirmed at host /proc level. Original GGUF file hashed by the physical host: SHA256 c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c (1,246,255,456 bytes).
- Independent 24-token direct completion canaries over private LAN and Tailscale each returned HTTP 200 and the exact expected marker. The existing process reports --parallel=4, but the operator-approved admission manifest deliberately declares **one slot** and queue_limit 0.
- No two-user request ownership receipt, host-signed boot/process witness or battery/Govee evidence was produced; those remain separate gates.

## Atomic AssistX approval actually applied

The repository's existing, unchanged `approve-runtime-projection.py` was used; script SHA256 5df9efa67d2292d978193915e9e9b0a9d04d884d8ba37f758b3b0733c8f800f3. The script supports dry-run, CAS on the expected canonical generation, and a Neo4j transaction that retires prior approvals and creates fresh runtime/model/path/capacity evidence before updating the canonical projection.

- Operator-source generation read from Neo4j READ_ACCESS: **642**.
- Proposed generation: **643**; operator approval ID references the user directive on 2026-10-11. The manifest records correct **8,192 loaded context**, Q8_0, exact GGUF SHA256, one-slot capacity, and verified private LAN+Tailscale paths.
- Source manifest SHA256 d55379406a8973a73102bb83d211a06331ac8e8cec825a2e1a22b48bc7a63afe. Private supporting operator proof SHA256 12a54d0a9e919388dd75e6fac6d13f9fc384004c41559b75e42887cc88b90916.
- Official dry-run **PASS**; authenticated official `--apply` CAS **PASS**. Applied generation **643**, 1 runtime, 1 loaded model, 2 access paths, 1 capacity observation, **900-second TTL**. Official approval receipt evidence SHA256 583a867c39bf51d7c0c597ef82c63bf37f99b5f66766cbfbdc4bbe53a2f01695.
- Exact absolute canonical expiry: **2026-10-11T17:16:38.815Z** (13:16:38.815 America/New_York). This is not continuously renewable by virtue of this chat.

Follow-up read-only Neo4j inspection found canonical generation 643 approved, with current runtime/model/path/capacity counts **1/1/2/1**, loaded model context [8192], quantization [Q8_0], capacity [1], queue [0], and transport [LAN, Tailscale].

Authenticated source acceptance:
- AssistX GET /api/router/runtime-projection **HTTP 200**, generation **643**, signature field present; it produced a short-lived signed projection.
- Auto-Router GET /admin/runtime-projection **HTTP 200**, fresh=true, current generation **643**, last_error cleared, normal polling rechecked fresh=true.
- Authenticated AssistX mobile runtime catalog **1 runtime and 1 model** (previously zero).
- Auto-Router /admin/admission view **1 runtime / 1 selected access path**, consistent with LAN preference. A cached admission view must not be treated as per-user execution proof.
- No production service restart, direct Cypher UPDATE, public listener change, GPS/NAS write, configuration override or external iPhone deployment occurred.

## Evidence custody

Operator-only manifest, private Beelink evidence, dry-run JSON and official approval JSON were copied byte-for-byte to an existing SSD-backed local state directory, NOT NAS/CIFS:

`/home/scott/.local/state/assistx-runtime-approvals/beelink-20261011T170045Z/`

Directory mode **0700**, files **0600**. These documents contain private service endpoint URLs and must **never** be committed to public GitHub. Verified local file SHA256s: approval-evidence.json **13d9564cde54f0e3fcba12f23f361108e135227cf34de4290f7287561b4e5e92**; manifest, proof and dry-run independently matched their source bytes.

## STILL OPEN — not solved by approval

1. **Durability:** This was a one-time, 15-minute approved generation. A genuinely source-backed periodic re-observation and authorized renewal is needed before declaring fleet projection permanently recovered. Do not silently renew expired approval by bumping Neo4j timestamps; any renewal must repeat host/model hash, fresh canaries, paths and physical identity checks, and honor operator review scope.
2. **Physical execution provenance:** A signed AssistX projection is not a provider-signed physical model completion witness, and generic operational readiness does not replace authenticated per-user request ownership. Auto-Router issue #37 two-user proof remains open.
3. **Security rollout:** Anonymous legacy Auto-Router GET /admin/providers returned HTTP 200 pre-approval; draft Auto-Router PR #41 and AssistX PR #288 still need client-first auth/fallback and independent 401 verification. The new effective providers endpoint returned HTTP 404 before deployment, as expected. No security PR promoted by this step.
4. **Real fleet cost/offline battery model:** Govee, UPS, DNS, router/switch survival, other nodes' process witnesses and task-verified performance metrics are not yet reconciled. This reattestation covers only Beelink's physically verified LFM model.
5. **Release authority:** Keep draft PRs #44/#46/#47/#49/#50 draft. No anonymous admin rollout, provider autoload, operator/iPhone exposure, or release promotion from these tests.

Related [P0 issue #52](https://github.com/scottjoyner/auto-router/issues/52), [research PR #50](https://github.com/scottjoyner/auto-router/pull/50), Auto-Router [issue #37](https://github.com/scottjoyner/auto-router/issues/37).
