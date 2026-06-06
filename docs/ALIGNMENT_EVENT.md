# Neo4j Context Alignment Event

## Purpose

This repo should route work using the same graph-backed context that AssistX owns.

Neo4j is the shared context fabric. The router should think in terms of graph objects—providers, models, services, nodes, capabilities, and provenance—not markdown vault fragments. Markdown is acceptable only as a derived cache/export, never as the coordination language.

## Shared contract

- AssistX remains the authoritative owner of task, event, and context state.
- auto-router remains the execution broker for OpenAI-compatible requests and agent workers.
- Local-only routing is always explicit.
- Free API credit usage is explicit and only happens when the registry says the lane is available.
- Providers and workers should be selected from graph-backed capability and locality facts, not from guesswork in request payloads.
- Shared markdown mounts and rsync-cached copies are not the source of truth for routing decisions.

## Context facts the router needs

- who is running locally;
- which nodes are allowed to stay local-only;
- which providers have legitimate free credit remaining;
- which models support the required capabilities;
- which lanes are blocked, reserved, or currently preferred;
- what provenance to attach to the response.

## Lane model

- `local`: LM Studio or other local endpoints.
- `free_api`: free cloud credits or brokered free lanes.
- `paperclip`: the current release path in AssistX for non-realtime execution.
- `blocked`: no valid execution surface yet.

## Router outputs

The router should return provenance that answers:

- which provider ran;
- which lane it used;
- whether the request stayed local;
- whether free credits were consumed;
- which context revision informed the choice.

## Next steps

1. Keep the graph projection in sync with AssistX/Neo4j so providers, models, services, and nodes remain first-class objects.
2. Point `AUTO_ROUTER_CONTEXT_CONFIG` at AssistX `/api/context/projection` for live sync when available, but treat that projection as a graph export rather than a markdown export.
3. Replace any remaining static YAML assumptions with graph-synced lane and capability data where practical.
4. Expose lane, locality, free-credit state, and graph object counts in `/health`, `/dashboard`, and admin endpoints.
5. Keep local-only requests and privacy-restricted requests from ever touching free cloud lanes.
