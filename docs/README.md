# Auto-Router Documentation

## Core design

- [`HIGH_LEVEL_DESIGN.md`](HIGH_LEVEL_DESIGN.md) — system context, strict-offline boundary, AssistX authority, major components, request/projection/reconciliation flows, security, and failure strategy.
- [`LOW_LEVEL_DESIGN.md`](LOW_LEVEL_DESIGN.md) — source layout, startup order, provider validation, projection replacement, priority admission, claim fencing, streaming lifecycle, discovery, Neo4j reconciliation, configuration, and tests.

## Operations and recovery

- [`fleet_outage_recovery.md`](fleet_outage_recovery.md) — operator recovery procedure for fleet discovery and routing incidents.

The root [`README.md`](../README.md) remains the quick-start and deployment reference. The HLD and LLD are the canonical design pair and must be updated when authority, identity, projection, admission, or reconciliation contracts change.
