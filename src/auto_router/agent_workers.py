from __future__ import annotations

import asyncio
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
    preferred_workers: list[str] = Field(default_factory=list)
    allow_write: bool = False
    allow_commit: bool = False
    allow_network: bool = True
    max_runtime_seconds: int = 1800
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

        The worker is fed the task text on stdin so simple CLI agents can be exercised without
        assuming a particular prompt format. It remains conservative: no commit/push support and
        no repository mutation orchestration beyond running in the provided workdir.
        """
        if not self.available():
            return AgentJobResult(
                job_id=job.job_id,
                worker_name=self.config.name,
                status="unavailable",
                summary="Worker is disabled or command is not installed.",
            )

        process = await asyncio.create_subprocess_exec(
            self.config.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            cwd=str(workdir),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(job.task.encode("utf-8")), timeout=job.max_runtime_seconds
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
