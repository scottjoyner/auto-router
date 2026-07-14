from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from auto_router.models import AgentWorkerConfig


class AgentJobRequest(BaseModel):
    job_id: str
    task: str
    repo_url: str | None = None
    repo_path: str | None = None
    branch: str | None = None
    priority: str = "repo_critical"
    task_kind: str | None = None
    capability_lane: str = "tool_required"
    requires_tools: bool = True
    evidence_required: bool = False
    evidence_bundle: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    enabled_toolsets: list[str] = Field(default_factory=list)
    preferred_workers: list[str] = Field(default_factory=list)
    allow_write: bool = False
    allow_commit: bool = False
    allow_network: bool = True
    max_runtime_seconds: int = 1800
    plan_steps: list[str] = Field(default_factory=list)
    validation_metrics: list[str] = Field(default_factory=list)
    review_checkpoints: list[str] = Field(default_factory=list)
    workflow_stage: str = "initial"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentJobResult(BaseModel):
    job_id: str
    worker_name: str
    status: str
    summary: str | None = None
    stdout: str = ""
    stderr: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


@dataclass
class AgentWorkerAdapter:
    config: AgentWorkerConfig

    def available(self) -> bool:
        return self.config.enabled and shutil.which(self.config.command) is not None

    async def run(self, job: AgentJobRequest, workdir: Path) -> AgentJobResult:
        """Run a CLI agent in a controlled workdir.

        The worker is fed a structured task envelope on stdin so tool-capable CLI agents can
        inspect task_kind, evidence requirements, allowed tools, and repo hints without assuming
        a particular prompt format. It remains conservative: no commit/push support and no
        repository mutation orchestration beyond running in the provided workdir.
        """
        if not self.available():
            return AgentJobResult(
                job_id=job.job_id,
                worker_name=self.config.name,
                status="unavailable",
                summary="Worker is disabled or command is not installed.",
            )

        task_payload = self._build_task_payload(job)
        launcher = (self.config.launcher or "stdin").strip().lower()
        toolsets = self._effective_toolsets(job)

        if launcher == "hermes_oneshot":
            command = [self.config.command]
            if self.config.model:
                command.extend(["-m", self.config.model])
            if self.config.provider:
                command.extend(["--provider", self.config.provider])
            if toolsets:
                command.extend(["-t", ",".join(toolsets)])
            if self.config.skills:
                command.extend(["--skills", ",".join(self.config.skills)])
            command.extend(["-z", task_payload])
            env = os.environ.copy()
            env.update({str(key): str(value) for key, value in (self.config.env or {}).items() if value is not None})
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir),
                env=env,
            )
            stdin_payload = None
        else:
            env = os.environ.copy()
            env.update({str(key): str(value) for key, value in (self.config.env or {}).items() if value is not None})
            process = await asyncio.create_subprocess_exec(
                self.config.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                cwd=str(workdir),
                env=env,
            )
            stdin_payload = task_payload.encode("utf-8")

        try:
            if stdin_payload is None:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=job.max_runtime_seconds)
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(stdin_payload), timeout=job.max_runtime_seconds
                )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return AgentJobResult(
                job_id=job.job_id,
                worker_name=self.config.name,
                status="timeout",
                summary="Worker exceeded max runtime.",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        summary = "Worker completed successfully." if process.returncode == 0 else "Worker exited with errors."
        artifacts = [
            {"kind": "stdout", "path": str(workdir / "stdout.txt")},
            {"kind": "stderr", "path": str(workdir / "stderr.txt")},
        ]
        return AgentJobResult(
            job_id=job.job_id,
            worker_name=self.config.name,
            status="succeeded" if process.returncode == 0 else "failed",
            summary=summary,
            stdout=stdout,
            stderr=stderr,
            artifacts=artifacts,
            usage={"returncode": process.returncode},
        )

    def _build_task_payload(self, job: AgentJobRequest) -> str:
        payload = {
            "job_id": job.job_id,
            "task": job.task,
            "task_kind": job.task_kind,
            "capability_lane": job.capability_lane,
            "requires_tools": job.requires_tools,
            "evidence_required": job.evidence_required,
            "allowed_tools": job.allowed_tools,
            "enabled_toolsets": job.enabled_toolsets,
            "plan_steps": job.plan_steps,
            "validation_metrics": job.validation_metrics,
            "review_checkpoints": job.review_checkpoints,
            "workflow_stage": job.workflow_stage,
            "allow_write": job.allow_write,
            "allow_commit": job.allow_commit,
            "allow_network": job.allow_network,
            "repo_url": job.repo_url,
            "repo_path": job.repo_path,
            "branch": job.branch,
            "context_note": job.metadata.get("context_note") if isinstance(job.metadata, dict) else None,
            "metadata": job.metadata,
            "evidence_bundle": job.evidence_bundle,
        }
        coordination_guidance = (
            "You are a Hermes-style finalizer and one member of a coordinated local-model team. "
            "Work one slice at a time. If the work is still exploratory, produce a small plan, execute the next step, "
            "and report the remaining gap instead of pretending the whole task is finished. "
            "If the work has been reviewed and finalized, validate the stated acceptance criteria and confirm the handoff state. "
            "Use the available tools conservatively and do not assume write/commit/network privileges beyond the task contract. "
            "Only use tools listed in enabled_toolsets.\n\n"
            "Preferred output shape:\n"
            "1. Situation summary\n"
            "2. Proposed plan\n"
            "3. Your assigned slice\n"
            "4. Dependencies or handoff needed from peers\n"
            "5. Validation steps / risks\n"
            "6. Next action\n\n"
            "If the task is simple, keep the same headings but answer briefly."
        )
        context_note = job.metadata.get("context_note") if isinstance(job.metadata, dict) else None
        context_block = ""
        if context_note:
            context_block = f"\n\nSOURCE CONTEXT START\n{context_note}\nSOURCE CONTEXT END\n"
        return (
            f"{coordination_guidance}\n\n"
            f"COORDINATION CONTEXT START\n"
            f"preferred_workers={json.dumps(job.preferred_workers)}\n"
            f"allowed_tools={json.dumps(job.allowed_tools)}\n"
            f"enabled_toolsets={json.dumps(job.enabled_toolsets)}\n"
            f"allow_write={job.allow_write}\n"
            f"allow_commit={job.allow_commit}\n"
            f"allow_network={job.allow_network}\n"
            f"workflow_stage={job.workflow_stage}\n"
            f"plan_steps={json.dumps(job.plan_steps)}\n"
            f"validation_metrics={json.dumps(job.validation_metrics)}\n"
            f"review_checkpoints={json.dumps(job.review_checkpoints)}\n"
            f"COORDINATION CONTEXT END\n\n"
            f"TASK CONTRACT START\n{json.dumps(payload, indent=2, sort_keys=True)}\nTASK CONTRACT END\n"
            f"\nTASK START\n{job.task}{context_block}\nTASK END\n"
        )

    def _effective_toolsets(self, job: AgentJobRequest) -> list[str]:
        toolsets = [*self.config.toolsets, *job.enabled_toolsets, *job.allowed_tools]
        return [tool for tool in dict.fromkeys(str(item).strip() for item in toolsets if str(item).strip())]
