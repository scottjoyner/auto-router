# Fleet Routing Policy

Goal: route work across local nodes, auto-router overlays, and public APIs while preserving the privacy wall for personal and internal data.

This policy is intentionally layered. A request is not pinned directly to a machine unless privacy, data locality, model availability, service load, or operator intent requires it. The normal flow is:

1. Classify the data and task.
2. Pick the preferred execution class: local-only, hybrid, or public-allowed.
3. Let auto-router and other available APIs overlay the preferred route when they have healthier, cheaper, faster, or more appropriate capacity.
4. Fail closed for private/internal data; fail open only for public/non-sensitive tasks.

## 1. Privacy and Data Classification

| Class | Examples | Allowed Executors | Cloud/Public API Use | Default Failure Mode |
|---|---|---|---|---|
| private | Sophia voice identity, Signal messages, personal files, raw call/SMS logs, credentials, home/family content | local agents only | no | fail closed |
| internal | Neo4j knowledge graph, auto-ingest records, unpublished repo state, trading/account details, internal service topology | local agents preferred; public APIs may receive redacted plans or generic code context only | only after redaction/approval | fail closed unless explicitly downgraded |
| operational | service health, routing metadata, model inventories, anonymous latency/health signals | local agents and auto-router overlays | yes, if no secrets or personal payloads | fail over |
| public | open-source docs, public web research, generic coding, generic planning | local, public APIs, or hybrid | yes | fail over |

Hard rule: personal content and internal data stay behind local agents. Public APIs can build plans, execute generic tasks, summarize public sources, and assist with non-sensitive code, but local models maintain the privacy wall.

## 2. Node Capability Matrix

| Node | Strengths | Constraints | Preferred Work | Avoid |
|---|---|---|---|---|
| x1-370 | strongest local reasoning node; large context possible because of 96 GB RAM; main AssistX/Neo4j/LM Studio services | shared services node, so do not overload; decoding/generation can bottleneck if KV cache is poorly managed | complex reasoning, large-context local/private tasks, privacy-sensitive planning, final local review | long speculative jobs that starve AssistX/Neo4j/Sophia; too many concurrent loaded dense models |
| deathstar-XPS-8920 | old auto-ingest and Neo4j origin; RX480 8 GB with good Vulkan behavior when model fits VRAM | slow if inference spills to system RAM; older rig | VRAM-fit inference, auto-ingest/Neo4j-local work, fast local generation with small/quantized models | models exceeding 8 GB VRAM; long memory-offloaded generation |
| Demo / falcon@Demo | Windows Strix Halo AMD AI 9 495; 8 GB VRAM plus 32 GB shared RAM; Hermes under WSL2 | Windows/WSL2 boundary can affect service paths, drivers, and startup behavior | flexible mid/high local node, Windows-side tasks, models that benefit from shared-memory headroom | Linux-only assumptions without WSL path/driver checks |
| MacBook Air M2 | fast for short prompts, quick prefill/generation, iterative small-model work; useful Sophia response node when online | severe 8 GB RAM limit; not a heavy reasoning host | quick drafts, prompt prep, structure/ideas, low-latency Sophia responses, iterative refinement loops | long context, large local models, memory-heavy jobs |

## 3. Model Selection Policy

Model choice should follow the task, not a single global default.

| Task Need | Preferred Model Class | Preferred Node(s) | Notes |
|---|---|---|---|
| complex reasoning / large context / private synthesis | Claude Opus distilled or strong Qwen dense model | x1-370 | keep service load moderate; avoid excessive concurrency |
| fast local generation where model fits GPU | MoE or smaller quantized models | deathstar, Demo, x1-370 if idle | prefer VRAM-fit paths; avoid RAM spill on deathstar |
| quick ideation / outlines / structure | LFM2.5-class fast generators or small instruct models | MacBook Air, deathstar, Demo | good for cheap iterations; not trusted as sole reasoner |
| Sophia realtime response | low-latency local model | MacBook Air if online, x1-370 fallback, Demo/deathstar as available | must respect voice/privacy wall |
| code/tool execution with non-sensitive context | best available via auto-router policy | auto-router overlay | public APIs may plan/execute if data is public or redacted |
| sensitive code or credentials-adjacent debugging | local-only model | x1-370, Demo, deathstar | no raw secrets to public APIs |

## 4. Routing Decision Tree

1. Is the payload private or personal?
   - Yes: set local_only=true / privacy=private. Use local agents only. Public API route is blocked.
   - No: continue.

2. Is the payload internal but redactable?
   - Yes: local model handles raw context. Public API may receive a redacted plan, abstracted error, or generic code task only if useful.
   - No or not worth redacting: local-only.

3. Does the task need heavy reasoning or large context?
   - Yes: prefer x1-370, but check service load and loaded model pressure first.
   - If x1-370 is busy: split into smaller local substeps or use public API for non-sensitive planning only.

4. Does the model fit a smaller GPU/node cleanly?
   - Yes: route to deathstar, Demo, or MacBook Air based on latency and model fit.
   - No: x1-370 or public API if data classification permits.

5. Is the task mostly planning, generic execution, or public-source work?
   - Yes: allow auto-router to pick public/local/hybrid route based on policy, quotas, health, and latency.

6. Is this Sophia or voice-adjacent?
   - Treat as private by default.
   - Prefer fast local node if online; avoid public APIs for personal content.

## 5. Auto-Router Overlay Rules

Auto-router is the policy overlay and observability point, not just a proxy.

Auto-router may override a preferred node when:
- the request is public or explicitly cloud-allowed;
- a provider has much better latency/quality for the task;
- local services are overloaded;
- quotas/free-tier availability make a public route cheaper or faster;
- AssistX context projection reports a healthier/fresher model endpoint;
- live model discovery shows a better loaded model on another node.

Auto-router must not override the route to cloud when:
- privacy=private;
- local_only=true;
- payload includes sensitive markers such as credentials, tokens, Signal content, voice auth, personal files, or raw internal knowledge graph data;
- the operator explicitly pins a local route.

Recommended logical aliases:

| Alias | Purpose | Privacy Behavior |
|---|---|---|
| auto/private | strict local-only | fail closed |
| auto/local | local LM Studio/fleet only | fail closed to local alternatives |
| auto/sophia | low-latency local Sophia path | private by default |
| auto/fast | normal interactive public-safe work | may use public APIs |
| auto/high-quality | local draft + stronger refine/judge where allowed | cloud only for non-sensitive/refined context |
| auto/code | code-focused path | cloud allowed only for public/redacted code |
| auto/flash-start | rapid public-safe planning | never raw private/internal payloads |

## 6. Load and Concurrency Guardrails

- Do not treat x1-370 as unlimited just because it has 96 GB RAM. It also hosts services.
- Prefer MoE variants on x1-370 when generation speed matters and KV cache pressure is high.
- Keep only a few models loaded concurrently; choose loaded models by current workload class.
- Prefer VRAM-fit models on deathstar. If it spills to RAM, route elsewhere unless no alternative exists.
- Use MacBook Air for short, iterative tasks rather than large context or heavy reasoning.
- For Sophia, prioritize latency and privacy over raw benchmark quality.

## 7. Fallback Policy

| Failure | Fallback |
|---|---|
| x1-370 overloaded | split task, use smaller local node for drafts, or public planning if non-sensitive |
| deathstar spills to RAM | move to Demo or x1-370; use smaller quant/MoE |
| MacBook Air offline | x1-370 for private Sophia work; Demo/deathstar if suitable |
| Demo WSL2 path/driver issue | fallback to x1-370 or deathstar depending on privacy/model fit |
| local LM Studio unhealthy | restart local service if safe; otherwise auto-router public fallback only for non-sensitive tasks |
| public API unavailable | local draft/refine if capacity exists; otherwise queue/defer |
| privacy classifier uncertain | local-only until explicitly reclassified |

## 8. Operational Signals to Feed Routing

Auto-router and AssistX should preserve these signals in routing decisions and provenance events:

- task_id / agent_run_id / node_id / assistx_source
- privacy / local_only / allow_cloud / sensitive flags
- selected provider, model, node, and endpoint
- loaded model inventory and context length
- provider health, probe timestamps, and latency
- route decision reason
- local service load for x1-370 when available
- Sophia voice/session sensitivity markers

Route decision traces should be metadata-only. Do not persist raw private prompts or personal payloads in public-facing logs.

## 9. Practical Defaults

Default behavior by task:

| Task | Default Route |
|---|---|
| personal/Sophia/Signal/raw knowledge graph | auto/private -> local node by latency/model fit |
| internal repo with secrets or proprietary context | auto/local -> x1-370/Demo/deathstar as appropriate |
| public coding task | auto/code -> public/local overlay |
| public planning/research | auto/flash-start or auto/fast |
| heavy private reasoning | x1-370 local, moderate concurrency |
| quick draft/structure | MacBook Air if online, then deathstar/Demo, then x1-370 |
| VRAM-fit small local generation | deathstar or Demo |
| final local privacy review | x1-370 or best available local reasoning model |

## 10. Implementation Notes

- Represent privacy as explicit request metadata, not an implicit convention.
- Keep redaction as a first-class step before any public API planning path.
- Let AssistX context projection provide live node/provider/service state when available; YAML bootstrap is a degraded fallback.
- Collapse duplicate provider/model endpoints into canonical provider-scoped IDs before routing.
- Keep discovered LM Studio endpoints and loaded model catalogs visible in ops summaries.
- If live context says revision=bootstrap when an AssistX projection URL is configured, treat routing context as degraded.

## 11. Operator Rule of Thumb

Use local agents for anything you would not paste into a public issue tracker. Use public APIs for plans, generic implementation help, public research, and execution that does not expose personal/internal data. Use auto-router as the overlay that decides the best live path inside those boundaries.
