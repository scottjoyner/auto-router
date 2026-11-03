# Starred witr review: canonical OpenAI catalog precedence for mixed-schema servers

**2026-11-03 · scoped read-only fleet audit · draft / production HOLD**

## Why inspect witr

The public starred inventory remained **1,068** repos (pages 1–10 each 100,
page 11 68). Evaluated [pranshuparmar/witr](https://github.com/pranshuparmar/witr)
(Apache-2.0), an on-demand process/port provenance CLI, source commit
`9e54a0507a4b3025fe8c3ecb329c7cb8da627995`, v0.3.4 Linux amd64
binary SHA-256
`d99098c61002f9b8a5337977d527dbe8ee29453f2a62c531038ea2749ea738eb`,
matching GitHub release metadata. A release checksum is **not** an
independent publisher signature or reproducible-build attestation.

The binary was copied transiently to `xwing` and `joyner` with pre-existing,
host-key-checked SSH, used without privileged access, without printing
process command lines or environment, then removed by a shell trap.
Cross-checks used only `ss`, `ps` and `/proc/PID/exe`.
No agent was installed, no services were restarted, and no
network listeners or provider settings were changed.

## Physical evidence and corrected schema finding

| Node | Listener evidence | Catalog evidence |
| --- | --- | --- |
| xwing | `ss` + `/proc` identify `lm-studio` PID 885908 bound to `0.0.0.0:1234` at inspection | Native LM Studio v1 16 records, native v0 17; `/v1/models` contains 17 canonical OpenAI `data[].id` records; **2 native-loaded models** |
| Joyner | `ss` identifies a **loopback** listener `127.0.0.1:1234` owned by `llama-server` PID 1400803; the LAN and tailnet bindings' owners were **not independently established** by nonroot inspection | Native LM Studio v1/v0 both 404. `/v1/models` and `/models` return **both** legacy `models` and canonical OpenAI `data` lists, each containing one corresponding model |

The model `id` in Joyner's `data[0]` is a nonempty
string with `object="model"`. Its value equals
the legacy `models[0].model` and `models[0].name`
in the inspected response. These identities were
**compared locally and not copied into this report**.

Joyner also answered GET `/health` with
`{"status":"ok"}`; `/props` responded, advertised a
nonempty chat template, and `is_sleeping=false`.
**Health and catalog compatibility are not generation tests**,
do not prove auth/tool calling, and do not constitute a
signed receipt of token usage or route admission.

The first diagnosis in this investigation was overly
restrictive: the old Auto-Router parser chose `models`
before `data`, so an initial inspection showed
an Ollama-shaped first record even though a valid
OpenAI `data` list was present. **The correct
fix is to prefer the canonical `data[].id`
while ignoring supplemental legacy `models`,
not to reject this dual-schema response.**

`witr --port 1234` identified xwing's process
ancestry under a desktop systemd scope, but
missed Joyner's port owner; Joyner has several
same-number bind addresses. The exact reason
for witr's miss is unproven. Negative witr
output is **not evidence of absent service**;
independent `ss` cross-checks are required.
PIDs are volatile, not stable provenance IDs.

## Auto-Router correction

The existing discovery code
`src/auto_router/fleet_task_dispatcher.py`
used `_inventory_rows()` for *both* native
LM Studio endpoints and `/v1/models`,
prioritizing `models` over `data` and
accepting `row.model` as a fallback ID.
That selected legacy schema instead of
canonical OpenAI `data[].id`, and could
promote a **different legacy model identity**
if the two fields disagreed.

This research branch is **stacked on**
[PR #115](https://github.com/scottjoyner/auto-router/pull/115),
base SHA
`4a61e3a1fab6c8fc255898aa66d16e2206969d98`.
It does **not** alter native residency rules or
production route configuration.

The new `_openai_compatible_rows()`
is used for `/v1/models` **only**.
It requires JSON `data` to be a list of
dictionaries with nonempty, bounded, unique,
control-free string `id` values. Optional
`object` markers, when present, must be
`list` (outer) and `model` (entry).
Invalid IDs or missing/invalid `data`
cause a sanitized
`v1/models: incompatible-model-schema`
diagnostic; no untrusted body is reflected.

A response carrying **both** `data` and
`models` is accepted **only if `data`
is independently valid**. The legacy
`models` array never creates model IDs
or runtime-residency assertions, and a
static warning records
`supplemental legacy models ignored; canonical data used`.
An invalid `data` list **cannot fall
back** to `models` and become routable.

If native LM Studio provides legitimate
residency evidence, it stays available
even if a separate compatible endpoint
is malformed. If only a compatible
OpenAI `data` list exists, the model is
*catalog-visible* but **residency unknown**:
`inventory_authoritative=false`,
`loaded_models=[]`.

## Corrected red/green and live validation

New regression file
`tests/test_witr_openai_endpoint_schema.py`
has **38 passing tests/parametrizations**
after the correction. Against the
*unchanged* PR #115 base with the
final corrected tests, **33 failed,
5 passed**: the prior parser
still mishandled strict canonical
model ID validation and dual-schema
precedence.

On the patched isolated research tree,
the combined discovery, native-residency,
live-model route, placement and authority
tests returned **129 passed, 4 skipped**.
Ruff lint, Ruff formatting and Python
AST checks passed. One preexisting
Starlette/httpx deprecation warning.

The final exact-head read-only `_probe_provider()`
GET-only comparison must maintain:

| Node | Valid catalog | Observed IDs | Native resident | Authoritative |
| --- | --- | ---: | ---: | --- |
| xwing | True | 17 | 2 | True |
| Joyner | **True** | **1 (canonical data ID)** | **0 measured / unknown residency** | **False** |

Joyner's `online=true` here means **valid
catalog schema**, not verified generation,
available memory, or route admission.

## No-go boundary / productive next step

**Draft / production HOLD.** No model
generation, memory allocation, account
registration, new paid provider, runtime
install, tracing-hook install, firewall
or route change was performed. Actual
prompt/completion tokens and useful
inference throughput remain unmeasured.

Joyner's existing LM Studio provider is
still disabled under separate identity,
policy and cutover controls. A bounded,
authorized canary against the **real
llama-server OpenAI chat/completions
contract** would be the next practical
step. It must establish actual model
identity, successful prompt/completion
tokens, tool-call correctness, latency
and source-side receipts, before an
independent signed SGNC custody and
operator admission decision.

Do not use `witr` ancestry, catalog
status, process presence, or `/health`
alone as a reason to enable the route.
