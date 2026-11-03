# Starred Local Studio discovery → Auto-Router native residency proof

**2026-11-03 (America/New_York)** · read-only fleet audit and regression hardening · **production HOLD**

## Starred-repository review

The public account still has **1,068 starred repositories** (100×10
plus 68 on page 11; page 12 empty).

Prioritized starred [sybil-solutions/local-studio](https://github.com/sybil-solutions/local-studio)
at exact commit `f65c8af78582f741b62546c93d7c66439f0cd5b1`.
Local Studio runs a tailnet-aware inference gateway/controller, and its
`apps/local-controller/src/discovery.ts` enumerates local listeners and
queries `GET /v1/models` on each. A nonempty list makes an endpoint
visible to its controller; this **does not prove that every advertised
model is resident, runnable, able to follow tools, or generating useful
tokens**. Installing its separate fleet controller would compete with
AssistX/Hermes/Auto-Router and add new keys and SSH-install
authority; **not authorized or required** for this test.

Also screened starred [microsoft/onnxruntime-genai](https://github.com/microsoft/onnxruntime-genai),
which lists LFM3 language architecture support for CPU execution
in its current README, while **native AMD GPU support remains a
roadmap item**, and starred [FlashML-org/FreeToken](https://github.com/FlashML-org/FreeToken),
whose released native GPU support targets NVIDIA RTX 30/40/50.
Neither vendor's listed benchmark can be extrapolated to the
Radeon R9700/ROCm or Ryzen AI iGPU/NPUs.

**Decision:** patch the existing Auto-Router observation path instead of
installing another service.

## Exact code defect reproduced before patch

At Auto-Router main SHA
`cf1a74b73bf25a1f39bb69c32758fdfbc740b606`,
`src/auto_router/fleet_task_dispatcher.py` used
`bool(row.get("loaded"))`. Thus the malformed JSON value
`{"loaded":"false"}` is **truthy Python** and was treated as a
loaded model. Presence of an unknown `state` field could
make a model appear measured unloaded, and **one** valid
native residency observation could cause the *entire* catalog
to be marked authoritative. Contradictory native API versions
were unioned into `loaded_models`.

A new `tests/test_local_studio_residency_proof.py` suite was
executed against this exact **unchanged** base snapshot using
local mocked HTTP, no real model generation or endpoint
write: **26 failed, 5 passed** before the fix. That is
confirmed red evidence, not a hypothetical defect.

## Fix (draft, not deployed)

`_loaded_from_row()` now returns strict **True, False, or None**:
- only recognized state strings are accepted;
- boolean keys must be actual JSON booleans, not
  `"true"`, `"false"`, integers or objects;
- instance lists must be lists of nonempty model
  instance objects; an empty list is valid measured
  non-residency;
- contradictory state, boolean and instance signals
  are **unknown**, not randomly resolved by precedence.

The read-only scanner merges up to three endpoints:
`/api/v1/models`, `/api/v0/models` and
`/v1/models`. Only native evidence can attest
residency. When the native APIs disagree, that
model is not placed into `loaded_models`;
when some catalog entries have no native
evidence, `inventory_authoritative` stays false.
Still report online visibility and totals so
diagnostics can show the discrepancy.

**Critical distinction:** a `loaded` observation is
*residency evidence*, not an end-to-end generation,
correct tool-call response, available headroom,
throughput, quality or cryptographic attestations.

## Real read-only fleet observations

From `x1-370`, only **GET** catalog probes were
issued, first against known remote hosts over
key-checked SSH to inspect their loopback APIs,
then using the exact configured **tailnet FQDNs**.
No credentials, API keys, prompts, model weights,
signed traces or raw media were exported to GitHub.

| Node | `/api/v1/models` | `/api/v0/models` | `/v1/models` | Measured loaded | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| `xwing` | 16 | 17 | 17 | **2** | Complete native evidence after merging both API versions; inventory authoritative |
| `joyner` | HTTP 404 | HTTP 404 | 1 | **Unknown** | Fallback-only; catalog visible, loaded status not measured |
| `x1-370` | Not verified in this comparison | Not verified in this comparison | 0 at local port 1234 | None verified | No local route added |

At `xwing`, native loaded instances
included one language model and one embedding
model; the other catalog entries were **not
16 additional inference workers**. The extra
17th native-v0 record resolves the apparent
mismatch between the 16-row native-v1
listing and the 17-row OpenAI-compatible list.
The exact fix properly reports the 17 observed
IDs as **2 resident models**, without declaring
all 17 loaded.

At `joyner`, the configured provider
`lmstudio-joyner` in `config/providers.yaml`
is **disabled**. Its OpenAI-compatible
`/v1/models` advertises one ID, but there
is no native LM Studio residency proof.
This is **not** a basis to enable the
provider, treat inference as actually
active, or bypass the ongoing Joyner
cutover/authentication and independent
trace-receipt gates.

The bare short aliases `xwing`/`joyner`
were **not** reliable direct access paths
from x1-370 in this run. The configured
`*.tailcb8954.ts.net` hostnames worked.
This is a name/address-path distinction,
not proof of broken serving.

## Validation

Local exact-branch test snapshots were assembled
in `/tmp`, never written to the live auto-router
checkout or deployed services.

- New mocked residency suite (now 32 individual
  tests/parametrizations) with malformed JSON,
  contradictory native endpoints, incomplete catalogs,
  native-v0 reconciliation and fallback-only liveness.
- Existing discovery, live-model routing, placement
  safety, fleet report and native residency tests.
- **91 PASSED, 4 SKIPPED, one existing
  StarletteDeprecationWarning**, local Py3.11
  virtualenv; AST parsing passed.
- The new test file passes Ruff.
  The touched original discovery module retains
  preexisting `E402`/import ordering findings
  because its module docstring sits *after*
  `from __future__ import annotations`.
  This PR deliberately avoids unrelated
  whole-file formatting churn; hosted CI
  is not yet confirmed.

## No-go and follow-up

**DRAFT / production HOLD.** No model was
loaded/unloaded, no GPU jobs or background
workers were started, no routing provider
was enabled/disabled, no graph writes,
no external paid provider and **zero real
model tokens were generated**.

Next smallest gates:
1. Use the existing trusted router credentials
   and independently verified model/route
   identity to qualify Joyner's **already
   configured but disabled** lane.
2. Perform a bounded, owner-approved actual
   generation and tool-call response test
   **with explicit model identity, token counts,
   latency and source-side receipt**. A
   model catalogue is not a benchmark.
3. Compare Auto-Router's complete native
   inventory against its configured desired
   providers and admission rules without
   changing service/network configuration.
4. Only consider routing changes under
   existing authorization and signed
   SensorGraft/SGNC evidence gates. Report
   unavailable or unauthorized capacity
   separately from idle resources.

This is targeted reliability work, not
a claim of increased token throughput.
