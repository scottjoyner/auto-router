# Router / AssistX / auto-assign boundary map

This document defines the ownership split that the migration plan is converging on.

## Canonical ownership

| Area | Owner | Notes |
|---|---|---|
| Provider/model routing | auto-router | Selects lanes, providers, and models; emits route decisions/provenance |
| Live inventory discovery | auto-router | Owns provider/model/service/CLI discovery and refresh loops |
| Event emission / outbox dispatch | auto-router | Emits metadata-only facts to AssistX/Neo4j and retries safely |
| Canonical task state | AssistX / Neo4j | Stores the authoritative task timeline, trace, and provenance |
| Claim / release / lease / heartbeat | auto-assign | Owns worker placement and lifecycle governance |
| Worker execution | paperclip | Executes work and reports completion/progress |
| Runtime context projection | AssistX / Neo4j | Supplies the router with live canonical context |

## What auto-router should not own

- canonical task lifecycle state
- lease and claim semantics
- assignment scheduler semantics
- worker placement authority
- a second task database

## Transitional code note

If a dispatcher lives inside auto-router, it is execution-consumer code only. It must not become a hidden scheduler or a second assignment authority.

## Practical rule

When in doubt, ask:

1. Does this decide routing? -> auto-router
2. Does this decide who owns a task? -> auto-assign
3. Does this store the canonical task history? -> AssistX / Neo4j
4. Does this execute the work? -> paperclip
