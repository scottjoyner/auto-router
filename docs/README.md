# Auto-Router Documentation

> **Document status index.** Every document below is classified
> `CURRENT`, `CURRENT_BUT_INCOMPLETE`, `HISTORICAL`, `SUPERSEDED`, or `PLAN_ONLY`.
> Source code and deployed configuration remain authoritative when a document
> disagrees with them. Classification was established by
> [`housekeeping/auto-router-config-and-dead-path-audit-2026-10-02.md`](housekeeping/auto-router-config-and-dead-path-audit-2026-10-02.md).

## Core design

| Document | Status | Notes |
|---|---|---|
| [`HIGH_LEVEL_DESIGN.md`](HIGH_LEVEL_DESIGN.md) | CURRENT | System context, strict-offline boundary, AssistX authority, components, request/projection/reconciliation flows, security, failure strategy. |
| [`LOW_LEVEL_DESIGN.md`](LOW_LEVEL_DESIGN.md) | CURRENT | Source layout, startup order, provider validation, projection replacement, priority admission, claim fencing, streaming lifecycle, discovery, configuration, tests. |
| [`HLD.md`](HLD.md) | SUPERSEDED | Pre-strict-offline HLD. Describes hosted/free-quota lanes and Neo4j write-back that no longer exist. Superseded by `HIGH_LEVEL_DESIGN.md`. |
| [`LLD.md`](LLD.md) | SUPERSEDED | Pre-strict-offline LLD. Superseded by `LOW_LEVEL_DESIGN.md`. Its route table lists `/admin/*` endpoints that are no longer mounted. |
| [`20260609-next-version-plan.md`](20260609-next-version-plan.md) | PLAN_ONLY | Cross-repo plan with a completed-session checklist. |

## Operations and recovery

| Document | Status | Notes |
|---|---|---|
| [`fleet_outage_recovery.md`](fleet_outage_recovery.md) | CURRENT | Operator recovery for fleet discovery and routing incidents. |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | CURRENT_BUT_INCOMPLETE | Aligned-deployment model is current. Does not describe `compose.reconciliation.yml`, `docker-compose.integration.yml`, or `secure_live:app`. |
| [`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md) | SUPERSEDED | Written for the pre-projection router (local YAML context, free hosted providers). Its `curl /admin/context` examples no longer reflect the strict-offline entrypoint. Use `DEPLOYMENT.md` + `HIGH_LEVEL_DESIGN.md`. |
| [`OPERATOR_RUNBOOK.md`](OPERATOR_RUNBOOK.md) | SUPERSEDED | Describes the "enhanced app wrapper", backlog scheduling, and service-launchpad surfaces that `main_live` deliberately does not mount. |
| [`TAILSCALE_DOCKER.md`](TAILSCALE_DOCKER.md) | CURRENT | Supported tailnet sidecar networking pattern. Companion reference is `examples/docker-compose.tailscale-sidecar.yml`. |
| [`AGENT_INFRA_VISIBILITY.md`](AGENT_INFRA_VISIBILITY.md) | CURRENT | Evidence contract for agents touching networking/ingress/Docker/service discovery. |

## Discovery, registry, and policy

| Document | Status | Notes |
|---|---|---|
| [`SERVICE_DISCOVERY.md`](SERVICE_DISCOVERY.md) | SUPERSEDED | Describes the service registry/scanner/model-registry split whose HTTP surfaces are not mounted by the production entrypoint. Concept remains useful as history. |
| [`FLEET_ROUTING_POLICY.md`](FLEET_ROUTING_POLICY.md) | SUPERSEDED | Layered local/hybrid/public routing policy. The public lane is forbidden in strict-offline mode. |
| [`ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md`](ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md) | CURRENT_BUT_INCOMPLETE | Ownership split is right; the "live inventory discovery → auto-router" row now belongs to AssistX projections. |
| [`HETEROGENEOUS_FLEET_ROUTING.md`](HETEROGENEOUS_FLEET_ROUTING.md) | CURRENT | Benchmark/worker-role aware projection routing. |
| [`HETEROGENEOUS_FLEET_ROUTING_CHECKLIST.md`](HETEROGENEOUS_FLEET_ROUTING_CHECKLIST.md) | CURRENT | Manual validation checklist. |
| [`fleet-loadouts.md`](fleet-loadouts.md) | CURRENT | Canonical Neo4j fleet-loadout shape; consumed by the transitional `scripts/build_fleet_loadouts.py`. |
| [`FLEET_EXPERIENCE_MEMORY.md`](FLEET_EXPERIENCE_MEMORY.md) | CURRENT | Memory extraction contract. |

## Retired capability surfaces (historical only — do not follow as runbooks)

| Document | Status | Notes |
|---|---|---|
| [`AGENTGATEWAY_INTEGRATION.md`](AGENTGATEWAY_INTEGRATION.md) | SUPERSEDED | Broker gateway integration. `AUTO_ROUTER_AGENTGATEWAY_ENABLED` is `false` in every canonical compose file; the `gateway-*` Makefile targets exit 2. |
| [`CEREBRAS_FLASH_NODE.md`](CEREBRAS_FLASH_NODE.md) | HISTORICAL | Hosted Cerebras flash-start node. Forbidden in strict-offline mode. |
| [`PROVIDER_MATRIX.md`](PROVIDER_MATRIX.md) | HISTORICAL | Hosted provider quota reference. |
| [`QUOTA_STRATEGY.md`](QUOTA_STRATEGY.md) | HISTORICAL | Hosted free-quota burn strategy. |
| [`ROUTER_SWITCHING_MECHANISM.md`](ROUTER_SWITCHING_MECHANISM.md) | HISTORICAL | Pre-strict-offline lane switching design. |
| [`ALIGNMENT_EVENT.md`](ALIGNMENT_EVENT.md) | HISTORICAL | Neo4j context alignment event record. |
| [`NEO4J_ASSISTX_INTEGRATION.md`](NEO4J_ASSISTX_INTEGRATION.md) | HISTORICAL | Direct-Neo4j integration design; superseded by HTTP projection over AssistX. |
| [`DASHBOARD.md`](DASHBOARD.md) | CURRENT_BUT_INCOMPLETE | Server-rendered dashboard design; the `/dashboard` route is mounted but its `/admin/*` JSON links are not admin-gated (see the audit's security findings). |
| [`SECURITY_PRIVACY.md`](SECURITY_PRIVACY.md) | CURRENT_BUT_INCOMPLETE | Principles are current; it predates Ed25519 projection/executor auth. |
| [`AGENT_SKILLS.md`](AGENT_SKILLS.md) | CURRENT | Discovery-is-not-authorization contract. |
| [`SWARM_UNIFICATION_BACKLOG.md`](SWARM_UNIFICATION_BACKLOG.md) | PLAN_ONLY | Cross-repo swarm execution backlog. |
| [`SWARM_UNIFICATION_CHECKLIST.md`](SWARM_UNIFICATION_CHECKLIST.md) | PLAN_ONLY | Swarm scope checklist. |
| [`SWARM_UNIFICATION_EXECUTION_PLAN.md`](SWARM_UNIFICATION_EXECUTION_PLAN.md) | PLAN_ONLY | Swarm execution ordering. |
| [`IDEAS.md`](IDEAS.md) | PLAN_ONLY | Future-work notes. |
| [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) | PLAN_ONLY | Pre-strict-offline implementation plan. |
| [`MEANINGFUL_OUTCOMES_ROADMAP.md`](MEANINGFUL_OUTCOMES_ROADMAP.md) | PLAN_ONLY | Fleet task pipeline phases. |
| [`TODO.md`](TODO.md) | HISTORICAL | Mostly-completed backlog from the pre-projection era. |

## Plans

`plans/` holds cross-repo implementation plans retained for provenance. They are
`PLAN_ONLY` unless a task in them has been implemented and linked from a CURRENT
document.

- [`plans/2026-06-08-auto-repos-orchestration-plan.md`](plans/2026-06-08-auto-repos-orchestration-plan.md) — PLAN_ONLY
- [`plans/2026-06-08-xwing-agent-development-handoff.md`](plans/2026-06-08-xwing-agent-development-handoff.md) — PLAN_ONLY
- [`plans/2026-07-02-auto-router-migration-plan.md`](plans/2026-07-02-auto-router-migration-plan.md) — PLAN_ONLY

## Housekeeping

- [`housekeeping/auto-router-config-and-dead-path-audit-2026-10-02.md`](housekeeping/auto-router-config-and-dead-path-audit-2026-10-02.md) — configuration authority map, runtime-projection v1/v2 disposition, Compose profile map, service-discovery dataflow, dead-code evidence, security findings.

The root [`README.md`](../README.md) remains the quick-start and deployment reference. The HLD and LLD are the canonical design pair and must be updated when authority, identity, projection, admission, or reconciliation contracts change.
