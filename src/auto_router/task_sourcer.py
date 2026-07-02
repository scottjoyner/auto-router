#!/usr/bin/env python3
"""
task_sourcer.py

Sources tasks from multiple repositories for the fleet task dispatcher:
1. Neo4j Task nodes (pending/review status)
2. Knowledge vault markdown files needing refinement
3. Auto-generated task ideas
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from neo4j import GraphDatabase


# Neo4j connection
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://100.64.43.123:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "knowledge_graph_2026")


def get_neo4j_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))


def _truncate_text(text: str, limit: int = 12_000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _task_context_note(props: dict) -> str | None:
    """Summarize useful task context without assuming a fixed Neo4j schema.

    The graph may carry transcript excerpts, source refs, notes, summaries, or
    other planning hints directly on the Task node. We fold the most helpful
    ones into a compact context block so downstream workers can see the
    originating transcript/planning hints without storing the full raw payload
    here.
    """
    if not isinstance(props, dict) or not props:
        return None

    parts: list[str] = []
    for key in ("summary", "description", "context", "transcript_excerpt", "source_excerpt", "source_text", "notes", "repo_name", "repo_path", "repository", "workspace_path"):
        value = props.get(key)
        if value:
            text = str(value).strip()
            if text:
                parts.append(f"{key}: {text}")

    refs: list[str] = []
    for key in ("transcript_id", "transcript_ref", "source_ref", "phone_log_id", "session_id", "conversation_id"):
        value = props.get(key)
        if value:
            refs.append(f"{key}={value}")

    if refs:
        parts.append("refs: " + ", ".join(refs))

    if not parts:
        return None

    note = "\n".join(parts)
    return _truncate_text(note, 5000)


def _markdown_file_prompt(md_file: Path, content: str) -> str:
    content = _truncate_text(content, 12_000)
    return (
        f"Review and refine this markdown file. Use the full file contents below, not just the filename.\n"
        f"File: {md_file}\n\n"
        f"FILE CONTENT START\n{content}\nFILE CONTENT END"
    )


def _looks_like_generated_task_artifact(md_file: Path, content: str) -> bool:
    """Skip self-generated task artifacts and prompt wrappers.

    The vault workspace also stores dispatcher outputs under `tasks/`, and those
    files often contain prompt scaffolding like `SOURCE CONTEXT START` or
    `Review and refine this markdown file`. Feeding them back into sourcing
    creates a recursive prompt loop instead of useful review work.
    """
    relative = md_file.as_posix().lower()
    if "/tasks/" in relative or relative.endswith("/tasks"):
        return True

    text = content.lower()
    markers = (
        "review and refine this markdown file",
        "you are the stronger reviewer model",
        "file content start",
        "source context start",
        "refined markdown file",
        "it seems you've provided a markdown file path",
    )
    return any(marker in text for marker in markers)


def _task_kind(props: dict) -> str:
    raw = str(props.get("task_kind") or props.get("kind") or props.get("category") or "").strip().lower()
    if raw:
        return raw.replace(" ", "_")

    text = " ".join(
        str(props.get(key) or "").lower()
        for key in ("summary", "description", "context", "notes", "title", "prompt")
    )
    keyword_map = [
        ("research", ("research", "investigate", "evidence", "source", "cite", "browse", "lookup")),
        ("analysis", ("analy", "measure", "metrics", "perf", "performance", "compare", "review")),
        ("refinement", ("refine", "rewrite", "polish", "edit", "summarize", "summary")),
        ("operations", ("deploy", "service", "ops", "monitor", "health", "diagnostic")),
        ("coding", ("code", "implement", "patch", "bug", "test", "refactor")),
    ]
    for kind, needles in keyword_map:
        if any(needle in text for needle in needles):
            return kind
    return "general"


def _task_evidence_bundle(props: dict) -> dict[str, object] | None:
    bundle: dict[str, object] = {}
    for key in (
        "summary",
        "description",
        "context",
        "notes",
        "transcript_excerpt",
        "source_excerpt",
        "source_text",
    ):
        value = props.get(key)
        if value:
            text = str(value).strip()
            if text:
                bundle[key] = text[:2000]

    refs: list[str] = []
    for key in ("transcript_id", "transcript_ref", "source_ref", "phone_log_id", "session_id", "conversation_id"):
        value = props.get(key)
        if value:
            refs.append(f"{key}={value}")
    if refs:
        bundle["refs"] = refs

    sources: list[str] = []
    for key in ("source_urls", "citations", "urls", "files", "paths"):
        value = props.get(key)
        if isinstance(value, list):
            sources.extend(str(item) for item in value[:10] if item)
        elif value:
            sources.append(str(value))
    if sources:
        bundle["sources"] = sources[:20]

    return bundle or None


def _task_contract(props: dict) -> dict[str, object]:
    task_kind = _task_kind(props)
    evidence_bundle = _task_evidence_bundle(props)
    requires_tools = bool(props.get("requires_tools")) if "requires_tools" in props else task_kind in {"research", "analysis", "operations"}
    evidence_required = bool(props.get("evidence_required")) if "evidence_required" in props else task_kind in {"research", "analysis"}
    capability_lane = str(props.get("capability_lane") or props.get("lane") or ("tool_required" if requires_tools else "prompt_only")).strip().lower()
    workflow_stage = str(props.get("workflow_stage") or props.get("stage") or "").strip().lower()
    if not workflow_stage:
        if bool(props.get("finalized")) or bool(props.get("reviewed")):
            workflow_stage = "handoff"
        elif requires_tools:
            workflow_stage = "iterative"
        else:
            workflow_stage = "prompt_only"
    if task_kind in {"code", "coding", "implementation", "refinement", "repair", "review", "repo", "patch"}:
        plan_steps = [
            "Inspect the current state one slice at a time.",
            "Make the smallest safe change or conclusion.",
            "Validate the result against the acceptance criteria.",
            "Report risks, gaps, and handoff notes.",
        ]
        validation_metrics = ["acceptance_criteria_met", "regressions_checked", "handoff_ready"]
    elif task_kind in {"research", "analysis", "documentation", "docs"}:
        plan_steps = [
            "Gather the relevant evidence or context.",
            "Compare the options and identify the best path.",
            "Draft the answer or recommendation.",
            "Verify claims, cite sources, and finalize the handoff.",
        ]
        validation_metrics = ["evidence_captured", "claims_supported", "final_answer_ready"]
    elif task_kind in {"operations", "terminal", "shell"}:
        plan_steps = [
            "Inspect the live state.",
            "Apply the smallest safe operation.",
            "Verify service health or output.",
            "Record what changed and what remains.",
        ]
        validation_metrics = ["state_verified", "change_applied", "health_confirmed"]
    else:
        plan_steps = [
            "Clarify the immediate goal.",
            "Advance the task in one small step.",
            "Validate the result.",
            "Summarize the next handoff.",
        ]
        validation_metrics = ["goal_understood", "next_step_defined", "handoff_ready"]
    review_checkpoints = ["reviewed by local iteration", "validated against plan", "final handoff approved"]
    return {
        "task_kind": task_kind,
        "requires_tools": requires_tools,
        "evidence_required": evidence_required,
        "capability_lane": capability_lane,
        "workflow_stage": workflow_stage,
        "plan_steps": plan_steps,
        "validation_metrics": validation_metrics,
        "review_checkpoints": review_checkpoints,
        "evidence_bundle": evidence_bundle,
    }


def get_tasks_from_neo4j(limit: int = 5) -> list[dict]:
    """Fetch pending/review tasks from Neo4j without mutating state."""
    driver = get_neo4j_driver()
    tasks = []

    try:
        with driver.session() as s:
            r = s.run(
                """
                MATCH (t:Task)
                WHERE t.status = 'REVIEW' OR t.status IS NULL
                RETURN t.id as id, t.title as title, t.status as status,
                       properties(t) as props, t.created_at as created_at
                ORDER BY t.created_at DESC
                LIMIT $limit
                """,
                limit=limit,
            ).data()

            for row in r:
                props = row.get("props") if isinstance(row.get("props"), dict) else {}
                tasks.append(
                    {
                        "source": "neo4j",
                        "task_id": row["id"],
                        "title": row["title"],
                        "status": row["status"],
                        "prompt": f"Review and refine this task: {row['title']}",
                        "context_note": _task_context_note(props),
                        **_task_contract(props),
                    }
                )
    finally:
        driver.close()

    return tasks


def claim_tasks_from_neo4j(limit: int = 5, claimer: str = "fleet_task_dispatcher") -> list[dict]:
    """Atomically claim REVIEW tasks so multiple workers don't duplicate work."""
    driver = get_neo4j_driver()
    tasks = []

    try:
        with driver.session() as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE t.status = 'REVIEW' OR t.status IS NULL
                WITH t ORDER BY t.created_at DESC
                LIMIT $limit
                SET t.status = 'IN_PROGRESS',
                    t.claimed_by = $claimer,
                    t.claimed_at = datetime()
                RETURN t.id as id, t.title as title, t.status as status,
                       properties(t) as props, t.created_at as created_at
                """,
                limit=limit,
                claimer=claimer,
            ).data()
            for row in rows:
                props = row.get("props") if isinstance(row.get("props"), dict) else {}
                tasks.append(
                    {
                        "source": "neo4j",
                        "task_id": row["id"],
                        "title": row["title"],
                        "status": row["status"],
                        "prompt": f"Review and refine this task: {row['title']}",
                        "context_note": _task_context_note(props),
                        **_task_contract(props),
                    }
                )
    finally:
        driver.close()

    return tasks


def complete_task_in_neo4j(
    task_id: str,
    *,
    status: str,
    completed_by: str | None = None,
    completed_node: str | None = None,
    completed_model: str | None = None,
    stage: str | None = None,
    response_path: str | None = None,
    draft_response_path: str | None = None,
    final_response_path: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: float | None = None,
    quality_score: float | None = None,
    response_chars: int | None = None,
    error: str | None = None,
    claimed_by: str | None = None,
    completion_source: str | None = None,
    task_kind: str | None = None,
    requires_tools: bool | None = None,
    evidence_required: bool | None = None,
    capability_lane: str | None = None,
    workflow_stage: str | None = None,
    plan_steps: list[str] | None = None,
    validation_metrics: list[str] | None = None,
    review_checkpoints: list[str] | None = None,
    evidence_bundle: dict[str, object] | None = None,
    outcome_state: str | None = None,
    outcome_reason: str | None = None,
    queue_wait_ms: float | None = None,
    dispatch_latency_ms: float | None = None,
) -> dict[str, Any] | None:
    """Persist metadata-only task completion state back to Neo4j.

    The dispatcher writes the full markdown artifact to the vault workspace.
    This write-back intentionally stores only operational metadata so the graph
    can track progress, provenance, and artifact pointers without duplicating the
    generated text itself.
    """
    driver = get_neo4j_driver()
    try:
        with driver.session() as s:
            rec = s.run(
                """
                MATCH (t:Task {id:$task_id})
                SET t.status = $status,
                    t.completed_by = coalesce($completed_by, t.completed_by),
                    t.completed_node = coalesce($completed_node, t.completed_node),
                    t.completed_model = coalesce($completed_model, t.completed_model),
                    t.task_stage = coalesce($stage, t.task_stage),
                    t.response_path = coalesce($response_path, t.response_path),
                    t.draft_response_path = coalesce($draft_response_path, t.draft_response_path),
                    t.final_response_path = coalesce($final_response_path, t.final_response_path),
                    t.input_tokens = coalesce($input_tokens, t.input_tokens),
                    t.output_tokens = coalesce($output_tokens, t.output_tokens),
                    t.latency_ms = coalesce($latency_ms, t.latency_ms),
                    t.quality_score = coalesce($quality_score, t.quality_score),
                    t.response_chars = coalesce($response_chars, t.response_chars),
                    t.claimed_by = coalesce($claimed_by, t.claimed_by),
                    t.completion_source = coalesce($completion_source, t.completion_source),
                    t.task_kind = coalesce($task_kind, t.task_kind),
                    t.requires_tools = coalesce($requires_tools, t.requires_tools),
                    t.evidence_required = coalesce($evidence_required, t.evidence_required),
                    t.capability_lane = coalesce($capability_lane, t.capability_lane),
                    t.workflow_stage = coalesce($workflow_stage, t.workflow_stage),
                    t.plan_steps = coalesce($plan_steps, t.plan_steps),
                    t.validation_metrics = coalesce($validation_metrics, t.validation_metrics),
                    t.review_checkpoints = coalesce($review_checkpoints, t.review_checkpoints),
                    t.evidence_bundle = coalesce($evidence_bundle, t.evidence_bundle),
                    t.outcome_state = coalesce($outcome_state, t.outcome_state),
                    t.outcome_reason = coalesce($outcome_reason, t.outcome_reason),
                    t.queue_wait_ms = coalesce($queue_wait_ms, t.queue_wait_ms),
                    t.dispatch_latency_ms = coalesce($dispatch_latency_ms, t.dispatch_latency_ms),
                    t.error = coalesce($error, t.error),
                    t.updated_at = datetime(),
                    t.updated_at_ts = timestamp(),
                    t.last_result_at = datetime(),
                    t.last_result_at_ts = timestamp()
                FOREACH (_ IN CASE WHEN $status IN ['DONE', 'FAILED', 'CANCELLED'] THEN [1] ELSE [] END |
                    SET t.completed_at = datetime(),
                        t.completed_at_ts = timestamp()
                )
                RETURN t.id AS id, t.status AS status, properties(t) AS props
                """,
                {
                    "task_id": task_id,
                    "status": status,
                    "completed_by": completed_by,
                    "completed_node": completed_node,
                    "completed_model": completed_model,
                    "stage": stage,
                    "response_path": response_path,
                    "draft_response_path": draft_response_path,
                    "final_response_path": final_response_path,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                    "quality_score": quality_score,
                    "response_chars": response_chars,
                    "claimed_by": claimed_by,
                    "completion_source": completion_source,
                    "task_kind": task_kind,
                    "requires_tools": requires_tools,
                    "evidence_required": evidence_required,
                    "capability_lane": capability_lane,
                    "workflow_stage": workflow_stage,
                    "plan_steps": plan_steps,
                    "validation_metrics": validation_metrics,
                    "review_checkpoints": review_checkpoints,
                    "evidence_bundle": evidence_bundle,
                    "outcome_state": outcome_state,
                    "outcome_reason": outcome_reason,
                    "queue_wait_ms": queue_wait_ms,
                    "dispatch_latency_ms": dispatch_latency_ms,
                    "error": error,
                },
            ).single()
            if not rec:
                return None
            props = {}
            try:
                raw_props = rec["props"]
                if isinstance(raw_props, dict):
                    props = raw_props
            except Exception:
                props = {}
            return {
                "id": rec["id"],
                "status": rec["status"],
                "props": props,
            }
    finally:
        driver.close()


def get_vault_tasks(limit: int = 5) -> list[dict]:
    """Find markdown files in vault workspace that need refinement."""
    vault_workspace = Path(os.getenv("LM_FLEET_VAULT_WORKSPACE", "/home/scott/knowledge/vault-workspace"))
    tasks = []

    if not vault_workspace.exists():
        return tasks

    for md_file in vault_workspace.rglob("*.md"):
        try:
            stat = md_file.stat()
            age_hours = (Path(__file__).parent.parent.stat().st_mtime - stat.st_mtime) / 3600

            if age_hours < 24 and md_file.stat().st_size > 100:
                file_text = _truncate_text(md_file.read_text(encoding="utf-8", errors="replace"), 12_000)
                if _looks_like_generated_task_artifact(md_file, file_text):
                    continue
                props = {
                    "summary": f"Refine this markdown file: {md_file.name}",
                    "path": str(md_file),
                    "source_text": file_text,
                    "source_excerpt": file_text[:2_000],
                    "task_kind": "refinement",
                    "requires_tools": False,
                    "evidence_required": True,
                    "capability_lane": "prompt_only",
                }
                tasks.append(
                    {
                        "source": "vault",
                        "task_id": str(md_file),
                        "title": md_file.stem,
                        "path": str(md_file),
                        "prompt": _markdown_file_prompt(md_file, file_text),
                        "context_note": f"File path: {md_file}\n\n{file_text}",
                        **_task_contract(props),
                    }
                )
        except Exception:
            continue

        if len(tasks) >= limit:
            break

    return tasks


def generate_task_ideas(limit: int = 5) -> list[dict]:
    """Generate task ideas for the fleet to work on."""
    ideas = [
        "Review the auto-router codebase and identify any potential issues or improvements.",
        "Summarize the key architectural decisions in the auto-router project.",
        "List the top 5 most important files in the auto-router codebase and explain their purpose.",
        "What are the main dependencies of the auto-router and why are they needed?",
        "Describe how the routing logic works in the auto-router.",
        "Identify security considerations in the fleet task dispatcher design.",
        "Suggest improvements to the EWMA metrics calculation for node scoring.",
        "Review the power profiles and suggest more accurate estimates.",
        "Document the task sourcing system for future developers.",
        "Propose a better quality validation threshold for response filtering.",
    ]

    tasks: list[dict] = []
    for i, idea in enumerate(ideas[:limit]):
        props = {
            "summary": idea,
            "task_kind": "analysis" if any(token in idea.lower() for token in ("review", "identify", "suggest", "what are", "describe", "compare")) else "general",
            "requires_tools": True,
            "evidence_required": True,
            "capability_lane": "tool_required",
        }
        tasks.append(
            {
                "source": "generated",
                "task_id": f"idea-{i}",
                "title": idea,
                "prompt": idea,
                **_task_contract(props),
            }
        )
    return tasks


def get_next_task() -> Optional[dict]:
    """Get the next task to dispatch."""
    neo4j_tasks = claim_tasks_from_neo4j(limit=1)
    if neo4j_tasks:
        return neo4j_tasks[0]

    vault_tasks = get_vault_tasks(limit=1)
    if vault_tasks:
        return vault_tasks[0]

    generated = generate_task_ideas(limit=1)
    return generated[0] if generated else None


def get_batch_tasks(count: int = 5) -> list[dict]:
    """Fetch multiple tasks for distribution across nodes."""
    all_tasks = []

    neo4j_tasks = claim_tasks_from_neo4j(limit=count)
    all_tasks.extend(neo4j_tasks)

    if len(all_tasks) < count:
        vault_tasks = get_vault_tasks(limit=count - len(all_tasks))
        all_tasks.extend(vault_tasks)

    if len(all_tasks) < count:
        generated = generate_task_ideas(limit=count - len(all_tasks))
        all_tasks.extend(generated)

    return all_tasks[:count]


if __name__ == "__main__":
    print("Task sourcing test:")
    print("\n1. Neo4j tasks:")
    for t in get_tasks_from_neo4j(limit=3):
        print(f"   {t['title'][:60]}")

    print("\n2. Vault tasks:")
    for t in get_vault_tasks(limit=3):
        print(f"   {t['title'][:60]}")

    print("\n3. Generated ideas:")
    for t in generate_task_ideas(limit=3):
        print(f"   {t['title'][:60]}")
