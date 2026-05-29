# Implementation Plan

## Phase 1

- Harden the FastAPI router with safer quota handling and provider health visibility.
- Keep the API-compatible surface working for local LM Studio clients.
- Add tests around quota accounting and routing decisions.

## Phase 2

- Replace the in-memory quota manager with Redis-backed atomic reservations.
- Add durable usage/audit storage in SQLite or Postgres-compatible schema.
- Normalize provider-specific responses and add streaming support.
- Add a graph-backed context projection from AssistX so local/free/paperclip lanes come from shared state instead of only YAML.

## Phase 3

- Turn agent workers into a real async job system with sandboxing and artifacts.
- Add retry/circuit-breaker behavior and richer dashboard views.
- Expand integration coverage around the real HTTP surface.
- Record execution provenance back into Neo4j so the router and AssistX share the same context fabric.
