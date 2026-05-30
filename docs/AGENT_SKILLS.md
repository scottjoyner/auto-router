# Agent Skills and Execution Contract

## 1. Purpose

This document defines the operating contract for code-agent CLIs such as Codex, Gemini CLI, and OpenCode when they are discovered by `auto-router` or registered by a remote node.

Discovery only answers whether a tool exists and is runnable. It does not authorize execution. Scheduling, write access, commits, pushes, and external side effects remain policy-gated.

## 2. Supported agent CLIs

| Skill | CLI command | Primary use | Default mode |
|---|---|---|---|
| `codex` | `codex` | premium repo-critical implementation/review | disabled unless subscription/quota and approval are available |
| `gemini_cli` | `gemini` | large-context review, docs, analysis, planning | review/dry-run |
| `opencode` | `opencode` | local coding-agent workflows | review/dry-run |

## 3. Skill states

| State | Meaning |
|---|---|
| `missing` | CLI command not found on the node |
| `installed` | CLI command exists on PATH |
| `runnable` | CLI version check succeeded |
| `blocked_by_policy` | CLI exists but scheduling policy does not allow use |
| `blocked_by_quota` | CLI exists but credits/subscription are unavailable |
| `review_only` | CLI may analyze and propose changes, but not write |
| `write_allowed` | CLI may write to a sandbox/worktree only |
| `commit_allowed` | CLI may commit only with explicit operator approval |
| `push_allowed` | CLI may push only with explicit operator approval |

## 4. Default safety posture

Default behavior for all agent CLIs:

- no repository mutation;
- no direct pushes;
- no production deploys;
- no credential reads;
- no `.env` reads unless explicitly scoped and local-only;
- no destructive shell commands;
- no background execution without a visible job record;
- no cloud use for local-only/private tasks;
- output must include a summary, patch intent, tests considered, and risks.

## 5. Allowed dry-run activities

Dry-run/review-only agents may:

- inspect repository files;
- summarize architecture;
- identify stale docs;
- propose patches as text;
- create TODO lists;
- produce implementation plans;
- recommend tests;
- analyze failures from provided logs;
- generate commands for an operator to run manually.

Dry-run/review-only agents may not:

- edit files;
- run write commands;
- create commits;
- push branches;
- create releases;
- call external deployment systems;
- modify databases;
- claim AssistX tasks.

## 6. Future write-mode requirements

Before any agent is allowed to write:

1. Work must be copied into an ephemeral worktree.
2. The task must include explicit write authorization.
3. The target repo/path must be allow-listed.
4. Secrets and `.env` files must be excluded.
5. Commands must pass an allow-list.
6. The agent must produce patch artifacts.
7. Tests must be captured.
8. Operator approval is required before commit or push.

## 7. Expected node registration payload

A node or router-local discovery process should report:

```json
{
  "name": "gemini-cli",
  "command": "gemini",
  "type": "gemini_cli",
  "installed": true,
  "runnable": true,
  "path": "/usr/local/bin/gemini",
  "node_id": "x1-370",
  "checked_at": 1760000000,
  "version": "gemini 1.0.0",
  "error": null,
  "credit_hint": "credits_remaining_expected",
  "notes": "Useful for large-context review and batch analysis."
}
```

This should be stored as:

```text
(SwarmNode)-[:HAS_AGENT_CLI]->(AgentCli)
(AgentCli)-[:EXPOSES_WORKER]->(AgentWorker)
```

## 8. Outbox events

CLI discovery emits:

```text
router.agent_cli.discovered
```

Future execution should emit:

```text
router.agent_run.planned
router.agent_run.started
router.agent_run.completed
router.agent_run.failed
router.agent_run.blocked
```

Execution payloads should include:

- task ID;
- repo/path scope;
- agent CLI name/type;
- node ID;
- mode: `dry_run`, `review_only`, `write_allowed`, `commit_allowed`;
- policy decision;
- artifacts;
- stdout/stderr refs;
- test results;
- operator approval refs.

Execution payloads must not include secrets or raw private data.

## 9. Router scheduling interpretation

The router should combine discovery, policy, quota, and task privacy:

```text
CLI missing                 -> unavailable
CLI installed+runnable      -> candidate capability
quota/credits exhausted     -> blocked_by_quota
subscription reset pending  -> blocked_by_quota
local_only task             -> local node only
sensitive/private task      -> local-only or skipped
write not approved          -> review_only
approval present            -> allow configured write mode
```

## 10. Current implementation status

Implemented:

- local CLI discovery for `codex`, `gemini`, and `opencode`;
- `/admin/agent-clis`;
- `/admin/agent-clis/discover`;
- `router.agent_cli.discovered` outbox events;
- dry-run backlog selection that does not execute agents.

Not implemented yet:

- remote node self-report endpoint;
- persisted AgentCli SQLite store separate from outbox;
- AssistX task claim/approval flow;
- agent worktree sandbox;
- command allow-list enforcement;
- write/commit/push execution modes.
