STATUS: PASS
TESTS:
- `PYTHONPATH=/tmp/opencode/pydantic_settings_shim python3 -m pytest -q tests/test_signed_projection_events.py tests/test_stream_lifecycle.py tests/test_route_events.py tests/test_admission.py tests/test_access_paths.py tests/test_joyner_ling_receipt_candidate.py` -> 71 passed
- Full reconciliation suite (reconciliation script args, run via `python3 -m pytest`): 757 passed, 3 skipped, 1 deselected, 16 failed — all 16 fail identically on the pristine tree in this environment (missing `pydantic-settings`/`pytest-asyncio` here; several are live-host drift gates). Zero regressions from this change; verified by `git stash -u` + re-run (16 failed, 16 passed, 757 baseline matches).
- `PYTHONPATH=/tmp/opencode/pydantic_settings_shim python3 -m pytest -q tests/test_joyner_ling_receipt_candidate.py tests/test_joyner_ling_signed_stream_witness.py` -> 34 passed
- `ruff check src/auto_router/route_events.py src/auto_router/stream_lifecycle.py tests/test_signed_projection_events.py` -> only pre-existing E501 at src/auto_router/stream_lifecycle.py:145 (present on HEAD, unrelated); new/changed lines clean.
- Validation note: this host lacks `pydantic-settings` and `pytest-asyncio` and network installs are forbidden, so the suite ran with a validation-only, uncommitted local shim (`/tmp/opencode/pydantic_settings_shim/pydantic_settings.py`, defaults-only BaseSettings) plus the repo's own `tests/conftest.py` asyncio runner. No repo files stub these packages.

Source evidence:
- Objective phrase "signed generation receipts versus attempt counts" maps to the outbox events that carry `runtime_projection_generation`: route decision/execution events copied raw caller metadata (forgeable), and stream lifecycle events hardcoded `None` (provenance never correlated). The signed AssistX projection is the only legitimate provenance source; `stream_lifecycle` docstring already treats request metadata as untrusted.
- Fix (`src/auto_router/route_events.py`): new `signed_projection_generation(state, request_metadata)` emits the generation only when it is a strict `int >= 1` exactly matching `state.runtime_projection_manager.current.generation`. `_projection_metadata` now sources generation from that helper and revision/checksum from the manager itself; on any mismatch/absence every field reports None (schema shape preserved). Both call sites (`enqueue_route_decision_event`, `enqueue_route_execution_event`) pass `state`.
- Fix (`src/auto_router/stream_lifecycle.py`): `router.stream.*` events now carry the same manager-verified generation instead of a hardcoded None, so stream receipts can be reconciled against attempt counts under the same fail-closed rule.
- New tests (`tests/test_signed_projection_events.py`, synthetic only — no sockets, models, credentials, or live hosts): live-projection match accepted; forged/mismatched/string/bool/absent claims rejected; missing live projection rejected; execution and decision events project manager values (not caller revision/checksum) and drop all forged claims; stream events carry the signed generation on match and None otherwise. The existing anti-spoofing test (`test_shared_endpoint_trace_cannot_spoof_model_instance_or_claim_from_request_body`) still passes unchanged.

Changed files:
- src/auto_router/route_events.py
- src/auto_router/stream_lifecycle.py
- tests/test_signed_projection_events.py
- Commit: de59680 `fix(events): stamp signed projection provenance, never caller claims` (branch research/opencode-fleet-router-20261104; not pushed, no PR created)
- FLEET_DELIVERY_BRIEF.md left untracked as found.

Remaining blockers / not done:
- No production readiness claim. Strict owner inference remains disabled pending a shared, cross-replica attempt/spend authority (`shared_owner_attempt_authority_unverified` in executor_auth.py); this delivery does not touch it.
- Route events still copy other caller-influenced fields (e.g. runtime_instance_id, selected_access_url) verbatim; only projection provenance was hardened here. Config-derived instance identity remains labelled `configured_route_not_runtime_attestation`.
- Live projector rollout, AssistX-side acceptance of the new event contents, deployment, and any PR merge/push remain operator actions and are out of scope.
- Physical fleet verification (generation roll/refresh, crash custody of receipts) not performed; only synthetic tests ran.

Safe PR proposal:
- Open a normal PR from `research/opencode-fleet-router-20261104` to `main` titled `fix(events): stamp signed projection provenance, never caller claims` with the commit de59680. Body: problem (forgeable/None projection provenance in outbox events), fix summary, test evidence (this file), explicit non-goals (no admission/dispatch/policy/config changes, no deployment, no live AssistX dependency — behavior identical when the live projection is absent: fields are None), and rollback note (revert commit; no schema or migration changes; event keys unchanged). Review focus: `validation in signed_projection_generation` (strict int, exact generation match) and the decision to take revision/checksum from the manager rather than metadata. Do not merge until CI (with its proper dependency set) is green and an operator signs off; this is not a production-readiness claim.

## Independent continuation — November 6, 2026, Fleet Commander

STATUS: PASS
TESTS: 79 passed, 1 pytest-config warning, 0 failed in ephemeral network-disabled read-only Docker `auto-assist-wt-ab3bb64-api:latest` with *real* `pydantic-settings==2.16.1` and the mounted host pytest harness. Focused modules: `tests/test_signed_projection_events.py`, `tests/test_stream_lifecycle.py`, `tests/test_route_events.py`, `tests/test_admission.py`, `tests/test_access_paths.py`, `tests/test_joyner_ling_receipt_candidate.py`.

- The same suite passed 79 tests on the lightweight host when using `/tmp/opencode/pydantic_settings_shim`, but the new container result does not rely on that shim. This is a stronger dependency witness, not a canonical Auto-Router image witness or hosted CI run.
- Container had no network, no write access to repo or root filesystem, no production secrets, 2 GiB memory and 2 CPU caps. Pytest was mounted read-only. `asyncio_mode` generated one warning because `pytest-asyncio` is absent; the repository's test harness handled the tests.
- Source identity at this verification: research branch HEAD `442c3f5`; no provider policy changes, free-endpoint traffic, credential changes, deployments, merges or PRs.
- Production remains HOLD: independent cross-replica attempt/spend authority, signed runtime projection, release approval and physical routing acceptance remain unverified.
