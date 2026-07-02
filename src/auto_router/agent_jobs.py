from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_router.agent_workers import AgentJobRequest, AgentJobResult, AgentWorkerAdapter
from auto_router.models import AgentWorkerConfig


@dataclass
class AgentJobRecord:
    request: AgentJobRequest
    status: str = "queued"
    worker_name: str | None = None
    result: AgentJobResult | None = None
    workdir: str | None = None
    artifact_paths: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class AgentJobManager:
    def __init__(self, agents: list[AgentWorkerConfig], base_dir: str | Path = "data/agent-jobs"):
        self.adapters = [AgentWorkerAdapter(config) for config in agents]
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, AgentJobRecord] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def submit(self, request: AgentJobRequest) -> AgentJobRecord:
        record = AgentJobRecord(request=request)
        self.jobs[request.job_id] = record
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return record
        self._tasks[request.job_id] = loop.create_task(self._run_job(request.job_id))
        return record

    def get(self, job_id: str) -> AgentJobRecord | None:
        return self.jobs.get(job_id)

    async def _run_job(self, job_id: str) -> None:
        record = self.jobs[job_id]
        request = record.request
        record.status = "running"
        workdir = Path(tempfile.mkdtemp(prefix=f"{job_id}-", dir=self.base_dir))
        record.workdir = str(workdir)
        self._write_json(workdir / "request.json", request.model_dump())

        adapter = self._choose_adapter(request)
        if adapter is None:
            record.status = "failed"
            record.error = "No available worker matched the request."
            self._write_json(workdir / "result.json", record_as_dict(record))
            return

        record.worker_name = adapter.config.name
        result = await adapter.run(request, workdir)
        record.result = result
        record.status = result.status
        record.artifact_paths = result.artifacts
        self._write_text(workdir / "stdout.txt", result.stdout)
        self._write_text(workdir / "stderr.txt", result.stderr)
        self._write_json(workdir / "result.json", result.model_dump())

    def _choose_adapter(self, request: AgentJobRequest) -> AgentWorkerAdapter | None:
        preferred = [name for name in request.preferred_workers if name]
        requested_toolsets = {tool for tool in request.enabled_toolsets if tool}
        if preferred:
            for name in preferred:
                for adapter in self.adapters:
                    if adapter.config.name != name or not adapter.available():
                        continue
                    if requested_toolsets and not requested_toolsets.issubset(set(adapter.config.toolsets or [])):
                        continue
                    return adapter
        for adapter in self.adapters:
            if not adapter.available():
                continue
            if requested_toolsets and adapter.config.toolsets and not requested_toolsets.issubset(set(adapter.config.toolsets)):
                continue
            return adapter
        return None

    def artifacts_for(self, job_id: str) -> list[dict[str, Any]]:
        record = self.jobs.get(job_id)
        if not record:
            return []
        if record.result:
            return record.result.artifacts
        return record.artifact_paths

    def list_records(self) -> list[AgentJobRecord]:
        return list(self.jobs.values())

    def _write_text(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def record_as_dict(record: AgentJobRecord) -> dict[str, Any]:
    payload = {
        "job_id": record.request.job_id,
        "status": record.status,
        "worker_name": record.worker_name,
        "error": record.error,
        "request": record.request.model_dump(),
        "artifacts": record.artifact_paths,
    }
    if record.result is not None:
        payload["result"] = record.result.model_dump()
    return payload


def _normalize_task_text(payload: dict[str, Any]) -> str:
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    task_values = [
        payload.get("task"),
        payload.get("prompt"),
        payload.get("title"),
        metadata.get("task"),
        metadata.get("prompt"),
        metadata.get("title"),
    ]
    return " ".join(str(part).strip() for part in task_values if part)


def _dedupe_workers(workers: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for worker in workers:
        if worker in seen:
            continue
        seen.add(worker)
        deduped.append(worker)
    return deduped


def _dedupe_strings(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _default_workflow_stage(payload: dict[str, Any]) -> str:
    explicit = payload.get("workflow_stage")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    stage = str(metadata.get("workflow_stage") or metadata.get("stage") or "").strip().lower()
    if stage:
        return stage
    if bool(metadata.get("finalized")) or bool(metadata.get("reviewed")):
        return "handoff"
    return "initial"


def _tool_worker_preferences(payload: dict[str, Any]) -> list[str]:
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    task_text = _normalize_task_text(payload).lower()
    task_kind = str(payload.get("task_kind") or metadata.get("task_kind") or "").strip().lower()
    workflow_stage = _default_workflow_stage(payload)
    repo_path = str(payload.get("repo_path") or metadata.get("repo_path") or "").strip().lower()
    repo_name = str(metadata.get("repo_name") or metadata.get("repository") or "").strip().lower()

    preferred: list[str] = []
    if "portfolio-management" in repo_path or repo_name == "portfolio-management" or "portfolio-management" in task_text:
        preferred.append("xwing")

    if workflow_stage in {"handoff", "final", "finalized", "review_final"}:
        preferred.extend(["hermes", "codex", "opencode", "gemini-cli"])
    elif task_kind in {"code", "implementation", "refinement", "repair", "review", "repo", "patch"} or any(
        token in task_text for token in ("fix", "patch", "implement", "refactor", "review code", "repository")
    ):
        preferred.extend(["hermes", "codex", "opencode"])
    elif task_kind in {"research", "analysis", "documentation", "docs"} or any(
        token in task_text for token in ("research", "analyze", "analysis", "compare", "summarize", "document")
    ):
        preferred.extend(["hermes", "gemini-cli", "codex"])
    elif task_kind in {"operations", "terminal", "shell"} or any(token in task_text for token in ("run", "inspect", "diagnose", "terminal")):
        preferred.extend(["hermes", "opencode", "codex"])

    explicit = payload.get("preferred_workers")
    if isinstance(explicit, list):
        for worker in explicit:
            worker_name = str(worker).strip()
            if worker_name:
                preferred.append(worker_name)

    return _dedupe_workers(preferred)


def _default_enabled_toolsets(payload: dict[str, Any]) -> list[str]:
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    explicit = payload.get("enabled_toolsets")
    if not isinstance(explicit, list):
        metadata_toolsets = metadata.get("enabled_toolsets")
        explicit = metadata_toolsets if isinstance(metadata_toolsets, list) else []
    cleaned = _dedupe_strings([str(item) for item in explicit])
    if cleaned:
        return cleaned

    task_kind = str(payload.get("task_kind") or metadata.get("task_kind") or "").strip().lower()
    workflow_stage = _default_workflow_stage(payload)
    if workflow_stage in {"handoff", "final", "finalized", "review_final"}:
        return ["file", "terminal", "session_search", "skills"]
    if task_kind in {"code", "implementation", "refinement", "repair", "review", "repo", "patch"}:
        return ["terminal", "file", "code_execution", "skills"]
    if task_kind in {"research", "analysis", "documentation", "docs"}:
        return ["web", "browser", "file", "terminal", "session_search", "memory", "skills"]
    if task_kind in {"operations", "terminal", "shell"}:
        return ["terminal", "file", "code_execution"]
    return []


def _default_allow_write(payload: dict[str, Any]) -> bool:
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    explicit = payload.get("allow_write")
    if isinstance(explicit, bool):
        return explicit
    return bool(metadata.get("allow_write") or metadata.get("write_access") or metadata.get("repo_write"))


def _default_allow_commit(payload: dict[str, Any]) -> bool:
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    explicit = payload.get("allow_commit")
    if isinstance(explicit, bool):
        return explicit
    return bool(metadata.get("allow_commit") or metadata.get("commit_access") or metadata.get("repo_commit"))


def _default_validation_metrics(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("validation_metrics")
    if isinstance(explicit, list) and any(str(item).strip() for item in explicit):
        return [str(item).strip() for item in explicit if str(item).strip()]
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    task_kind = str(payload.get("task_kind") or metadata.get("task_kind") or "").strip().lower()
    if task_kind in {"code", "implementation", "refinement", "repair", "review", "repo", "patch"}:
        return ["acceptance_criteria_met", "regressions_checked", "handoff_ready"]
    if task_kind in {"research", "analysis", "documentation", "docs"}:
        return ["evidence_captured", "claims_supported", "final_answer_ready"]
    if task_kind in {"operations", "terminal", "shell"}:
        return ["state_verified", "change_applied", "health_confirmed"]
    return ["goal_understood", "next_step_defined", "handoff_ready"]


def _default_allow_network(payload: dict[str, Any]) -> bool:
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    explicit = payload.get("allow_network")
    if isinstance(explicit, bool):
        return explicit
    if "allow_network" in metadata and isinstance(metadata["allow_network"], bool):
        return metadata["allow_network"]
    return True


def _default_plan_steps(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("plan_steps")
    if isinstance(explicit, list) and any(str(item).strip() for item in explicit):
        return [str(item).strip() for item in explicit if str(item).strip()]
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    task_kind = str(payload.get("task_kind") or metadata.get("task_kind") or "").strip().lower()
    if task_kind in {"code", "implementation", "refinement", "repair", "review", "repo", "patch"}:
        return [
            "Inspect the current state one slice at a time.",
            "Make the smallest safe change or conclusion.",
            "Validate the result against the acceptance criteria.",
            "Report risks, gaps, and handoff notes.",
        ]
    if task_kind in {"research", "analysis", "documentation", "docs"}:
        return [
            "Gather the relevant evidence or context.",
            "Compare the options and identify the best path.",
            "Draft the answer or recommendation.",
            "Verify claims, cite sources, and finalize the handoff.",
        ]
    if task_kind in {"operations", "terminal", "shell"}:
        return [
            "Inspect the live state.",
            "Apply the smallest safe operation.",
            "Verify service health or output.",
            "Record what changed and what remains.",
        ]
    return [
        "Clarify the immediate goal.",
        "Advance the task in one small step.",
        "Validate the result.",
        "Summarize the next handoff.",
    ]


def _worker_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    task_text = _normalize_task_text(payload)
    repo_path = payload.get("repo_path") or metadata.get("repo_path")
    repo_url = payload.get("repo_url") or metadata.get("repo_url")
    branch = payload.get("branch") or metadata.get("branch")
    context_note = payload.get("context_note") or metadata.get("context_note")
    task_kind = str(payload.get("task_kind") or metadata.get("task_kind") or metadata.get("kind") or "tool_task").strip()
    capability_lane = str(payload.get("capability_lane") or metadata.get("capability_lane") or "tool_required").strip()
    requires_tools = bool(payload.get("requires_tools", metadata.get("requires_tools", True)))
    evidence_required = bool(payload.get("evidence_required", metadata.get("evidence_required", False)))
    evidence_bundle = payload.get("evidence_bundle")
    if evidence_bundle is None:
        evidence_bundle = metadata.get("evidence_bundle") or {}
    if not isinstance(evidence_bundle, dict):
        evidence_bundle = {"value": evidence_bundle}
    allowed_tools = payload.get("allowed_tools")
    if not isinstance(allowed_tools, list):
        allowed_tools = metadata.get("allowed_tools") if isinstance(metadata.get("allowed_tools"), list) else []
    enabled_toolsets = payload.get("enabled_toolsets")
    if not isinstance(enabled_toolsets, list):
        enabled_toolsets = metadata.get("enabled_toolsets") if isinstance(metadata.get("enabled_toolsets"), list) else []
    enabled_toolsets = _default_enabled_toolsets({**payload, "enabled_toolsets": enabled_toolsets, "metadata": metadata})
    if not allowed_tools:
        allowed_tools = enabled_toolsets
    allowed_tools = _dedupe_strings(allowed_tools)
    enabled_toolsets = _dedupe_strings(enabled_toolsets)
    workflow_stage = _default_workflow_stage(payload)
    plan_steps = _default_plan_steps(payload)
    validation_metrics = _default_validation_metrics(payload)
    review_checkpoints = _default_review_checkpoints(payload)

    agent_payload = {
        "job_id": payload.get("job_id") or str(uuid.uuid4()),
        "task": task_text,
        "repo_url": repo_url,
        "repo_path": repo_path,
        "branch": branch,
        "priority": payload.get("priority") or metadata.get("priority") or "repo_critical",
        "task_kind": task_kind,
        "capability_lane": capability_lane,
        "requires_tools": requires_tools,
        "evidence_required": evidence_required,
        "evidence_bundle": evidence_bundle,
        "allowed_tools": allowed_tools,
        "enabled_toolsets": enabled_toolsets,
        "plan_steps": plan_steps,
        "validation_metrics": validation_metrics,
        "review_checkpoints": review_checkpoints,
        "workflow_stage": workflow_stage,
        "allow_write": _default_allow_write(payload),
        "allow_commit": _default_allow_commit(payload),
        "allow_network": _default_allow_network(payload),
        "context_note": context_note,
        "metadata": {
            **metadata,
            "source_route": payload.get("source_route") or metadata.get("source_route") or "assistx",
            "task_kind": task_kind,
            "capability_lane": capability_lane,
            "requires_tools": requires_tools,
            "evidence_required": evidence_required,
            "allowed_tools": allowed_tools,
            "plan_steps": plan_steps,
            "validation_metrics": validation_metrics,
            "review_checkpoints": review_checkpoints,
            "workflow_stage": workflow_stage,
            "repo_path": repo_path,
            "repo_url": repo_url,
            "branch": branch,
            "context_note": context_note,
        },
    }
    preferred_workers = _tool_worker_preferences(agent_payload)
    if preferred_workers:
        agent_payload["preferred_workers"] = preferred_workers
    else:
        agent_payload.setdefault("preferred_workers", [])
    return agent_payload


def _default_review_checkpoints(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("review_checkpoints")
    if isinstance(explicit, list) and any(str(item).strip() for item in explicit):
        return [str(item).strip() for item in explicit if str(item).strip()]
    return ["reviewed by local iteration", "validated against plan", "final handoff approved"]


def build_agent_job_request(body: dict[str, Any]) -> AgentJobRequest:
    payload = _worker_job_payload(dict(body))
    payload.setdefault("job_id", str(uuid.uuid4()))
    return AgentJobRequest.model_validate(payload)
