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

        This is intentionally conservative: it only executes disabled-by-default workers,
        captures output, and does not permit commits/pushes. Tool-specific prompt formatting
        will be added in the next implementation pass.
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
            cwd=str(workdir),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=job.max_runtime_seconds
            )
        except TimeoutError:
            process.kill()
            return AgentJobResult(
                job_id=job.job_id,
                worker_name=self.config.name,
                status="timeout",
                summary="Worker exceeded max runtime.",
            )

        return AgentJobResult(
            job_id=job.job_id,
            worker_name=self.config.name,
            status="succeeded" if process.returncode == 0 else "failed",
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            usage={"returncode": process.returncode},
        )
