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
        preferred = {name for name in request.preferred_workers if name}
        if preferred:
            for adapter in self.adapters:
                if adapter.config.name in preferred and adapter.available():
                    return adapter
        for adapter in self.adapters:
            if adapter.available():
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


def build_agent_job_request(body: dict[str, Any]) -> AgentJobRequest:
    payload = dict(body)
    payload.setdefault("job_id", str(uuid.uuid4()))
    payload.setdefault("preferred_workers", [])
    payload.setdefault("metadata", {})
    return AgentJobRequest.model_validate(payload)
