# Fleet runtime-projection recovery — read-only checkpoint 2026-10-11

**P0 BLOCKED. All diagnosis done through Fleet Commander on x1-370. No runtime, router, NAS or Neo4j mutations performed.**

## Verified findings

Read-only probes at 2026-10-11 16:25–16:40 UTC:

- Production Auto-Router and AssistX API both returned HTTP 200 on /health.
- Router's authenticated /admin/admission advertised one *cached* runtime; its signed runtime projection was CONFIGURED but NOT FRESH, with a recent upstream HTTP 503 error.
- Authenticated AssistX GET /api/router/runtime-projection returned HTTP 503. Application error was identified against the deployed source constant: **canonical projection approval evidence is expired**.
- AssistX fleet nodes = 0, inference models = 0, mobile runtime catalog = 0.
- The broader operations-readiness API reported ready=true and safe_to_dispatch=true. These flags do not prove this specific runtime projection or per-user execution admission is ready.
- Legacy router GET /admin/providers anonymously returned HTTP **200**, while /admin/providers/effective returned **404**, consistent with the still-draft authenticated catalog rollout. Anonymous GET /admin/admission correctly returned 401.
- The existing auto-router /nas worktree had 228 changed tracked files and was NOT touched. No NAS mount was assumed authoritative and no NAS data was written.

Using Neo4j READ_ACCESS, canonical FleetProjectionState with name canonical had status approved, generation **642**, approver ID/approval ID/manifest checksum present, but the approval expired about **5,393 minutes** (~90 hours) before the observation.

| Source label | Total | Approved/admitted | Currently valid approved/admitted |
|---|---:|---:|---:|
| RuntimeInstance | 20 | 1 | **0** |
| LoadedModelInstance | 44 | 1 | **0** |
| AccessPath | 35 | 2 | **0** |
| CapacityObservation | 2,451 | 1 | **0** |

**Two sequential blockers:** (1) canonical approval is expired; (2) the four dependent runtime/model/path/capacity evidence families all have zero fresh approved entries. Blindly extending the canonical expiry cannot repair the dependent evidence. A cached local runtime or visible /v1/models inventory is NOT physical LOADED evidence.

## Safe recovery sequence (operator-authorized, not performed)

1. **Host-backed re-observation**: independently observe physical node/runtime process, loaded model digest/revision, current health and completed test, capacity, approved LAN/Tailscale paths, and link ownership. Do not infer a physical model from localhost, LM Studio Link, or catalog AVAILABLE.
2. **Fresh canonical evidence**: ingest only source-validated, currently authorized runtime, LOADED model, access path, capacity and lease facts. Bind physical process and model to actual source and owner; verify reservation isolation and revocation. Each row needs a separately audited approval/expiry.
3. **Approved projection refresh**: obtain a new canonical fleet projection generation/revision and properly reviewed signed approval using the EXISTING AssistX-authorized workflow. Verify approver, approval ID and checksum, and current expiry. **No Neo4j UPDATE, fake signatures, status override or blind expiry bump is authorized by this research.**
4. **Read-only post-approval gate**: independently confirm one or more CURRENT valid records in all four families tied to the same runtime; authenticated AssistX /api/router/runtime-projection returns 200 with current source authority; router's authenticated /admin/runtime-projection returns fresh=true after normal polling; mobile inventory is independently validated against actual loaded status.
5. **Stage security/client rollout separately**: AssistX draft PR #288 is client-first, authenticated GET with *404-only* backward-compatible fallback. Review every caller, deploy with rollback, then review Auto-Router draft PR #41. Anonymous legacy GET /admin/providers must return **401**, new effective route authenticated GET 200. Effective catalog remains CONFIG ONLY, not admitted loaded/model physical proof.
6. **User/execution attestation still open**: Auto-Router issue #37 requires two authenticated subjects, owner-bound request IDs and physical provider execution witnesses. No synthetic shadow receipts or local hashes close it.
7. **Release gate**: operator review/approval, evidence provenance, live workload canary and rollback before any deployment, iPhone exposure, automatic routing or NAS ingestion.

The operations-readiness endpoint has broader semantics; reporting ready=true does not by itself prove a bug in that endpoint, but it is an important divergence from the runtime-projection requirement. Product UI should display the specific runtime gate instead of conflating them.

## Reproducible offline guard

Run without credentials, HTTP, Neo4j or model imports:

    python3 -m unittest discover -s tests -p 'test_fleet_authority_recovery_gate.py' -q
    python3 experiments/fleet_authority_recovery_gate.py experiments/fixtures/fleet_authority_observed_20261011.json

The read-only sanitized JSON is an **unsigned historic observation** — no owner IDs, node addresses, tokens, private runtime projection or raw telemetry. It is not a durable source of runtime authority. The evaluator rejects unrecognized/private fields; it produces status BLOCKED and specific reasons including canonical expiry and all four empty-current evidence classes. Even when all input checks are green, its output permanently has can_dispatch=false, can_promote=false, and execution_attested=false.

No live source adapter, producer reattestation, signer or atomic dispatcher has been implemented or invoked.

Related work: Auto-Router draft PRs #41, #44, #46, #47, #49, #50; AssistX draft PR #288; Auto-Router issues #37 and #43.
