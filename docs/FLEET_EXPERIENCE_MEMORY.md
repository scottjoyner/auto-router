# Fleet Experience Memory

## Status

This is the first extraction-friendly implementation of fleet experience
memory. It lives in `auto-router` until its HTTP contracts and operational
behavior are proven, then moves to `auto-memory`.

AssistX/Neo4j remains the intended canonical authority. The router-local SQLite
store is an idempotent cache and degraded-mode retrieval path; it does not
replace graph ownership.

## Data flow

```text
Codex / OpenCode / Hermes / Paperclip
  -> POST /api/memory/events
  -> local idempotent cache
  -> configured memory service (AssistX/Neo4j)

AssistX route request
  -> POST /api/memory/context
  -> remote graph retrieval when configured
  -> local lexical fallback on timeout/failure
  -> bounded context attached to route/job metadata
```

Memory lookup cannot block routing indefinitely. Remote failures degrade to
the local store and are exposed in the returned warnings and preflight report.

## API

### Ingest an observation, failure, resolution, or lesson

`POST /api/memory/events` requires the existing admin authentication.

```json
{
  "event_id": "codex:session-42:lesson-1",
  "source": "codex",
  "record": {
    "memory_id": "lesson:auto-router:assistx-ownership",
    "kind": "lesson",
    "summary": "AssistX owns canonical task state; auto-router only selects routes.",
    "repository": "scottjoyner/auto-router",
    "commit_sha": "abc123",
    "confidence": 0.9,
    "successful_reuses": 1,
    "tags": ["assistx", "ownership"],
    "evidence": [
      {
        "evidence_type": "repository_document",
        "reference": "docs/ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md"
      }
    ]
  }
}
```

`source + event_id` is idempotent. Replaying identical content succeeds without
duplicating it; reusing the same identifier with different content returns 409.

### Assemble context

`POST /api/memory/context` requires the existing admin authentication:

```json
{
  "query": "Add task claiming to backlog execution",
  "repository": "scottjoyner/auto-router",
  "task_id": "task-42",
  "limit": 8,
  "budget_tokens": 3000,
  "privacy_class": "local_only"
}
```

The response includes ranked records, evidence, the bounded text intended for
agent context, backend identity, degraded state, and warnings.

### Operator summary

`GET /admin/memory` requires admin authentication and reports local cache
counts plus remote configuration state.

## Configuration

```bash
AUTO_ROUTER_MEMORY_ENABLED=true
AUTO_ROUTER_MEMORY_SERVICE_URL=http://assistx:8000
AUTO_ROUTER_MEMORY_TIMEOUT_SECONDS=5
AUTO_ROUTER_MEMORY_CONTEXT_BUDGET_TOKENS=3000
AUTO_ROUTER_MEMORY_QUERY_LIMIT=8
```

Leave `AUTO_ROUTER_MEMORY_SERVICE_URL` empty for local degraded mode.
Queries classified `local_only` are forwarded only when this URL resolves to
localhost, a private IP, a single-label container hostname, `.lan`, `.local`,
or `.ts.net`; otherwise the router uses its local cache and emits a warning.

The configured remote service must implement:

```text
POST /v1/memory/events
POST /v1/memory/context
```

These `/v1` contracts are the extraction seam for the future `auto-memory`
service.

## Extraction checklist

1. Move `memory_models`, `memory_store`, and the remote API into `auto-memory`.
2. Implement Neo4j repository, task, execution, evidence, and lesson relations.
3. Add vector candidate retrieval behind the same context response contract.
4. Point `AUTO_ROUTER_MEMORY_SERVICE_URL` at the new service.
5. Retain only `memory_client` and router integration here.
6. Migrate cached events using their stable `source + event_id` keys.
