from __future__ import annotations

from typing import Any

import httpx

from auto_router.backlog_scheduler import BacklogTaskCandidate
from auto_router.models import Priority


class AssistXTaskClient:
    """Client for reading backlog candidates from AssistX.

    This client is intentionally read-only. It normalizes AssistX task payloads
    into dry-run scheduler candidates and does not claim, mutate, or dispatch
    AssistX tasks.
    """

    def __init__(self, base_url: str | None, timeout_seconds: float = 10.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def fetch_backlog_candidates(
        self,
        limit: int = 25,
        queue: str = "backlog",
        dry_run: bool = True,
    ) -> list[BacklogTaskCandidate]:
        if not self.base_url:
            raise RuntimeError("AUTO_ROUTER_ASSISTX_TASKS_URL is not configured")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                self.base_url,
                params={"limit": limit, "queue": queue, "dry_run": str(dry_run).lower()},
            )
        response.raise_for_status()
        payload = response.json()
        raw_tasks = _extract_tasks(payload)
        return [normalize_assistx_task(item) for item in raw_tasks[:limit]]


def normalize_assistx_task(item: dict[str, Any]) -> BacklogTaskCandidate:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    task_id = str(item.get("task_id") or item.get("id") or item.get("uuid") or metadata.get("task_id") or "")
    title = str(item.get("title") or item.get("name") or item.get("summary") or task_id or "AssistX task")
    prompt = str(item.get("prompt") or item.get("description") or item.get("body") or title)
    raw_priority = str(item.get("priority") or metadata.get("priority") or "background")
    priority = _priority(raw_priority)
    privacy = str(item.get("privacy") or item.get("privacy_label") or metadata.get("privacy") or "").lower()
    local_only = bool(item.get("local_only") or metadata.get("local_only") or privacy in {"local_only", "private", "secret"})
    sensitive = bool(
        item.get("sensitive")
        or metadata.get("sensitive")
        or privacy in {"private", "secret", "voice_auth", "enrollment", "enrollment_sample"}
    )
    allow_cloud_raw = item.get("allow_cloud", metadata.get("allow_cloud", None))
    allow_cloud = allow_cloud_raw if isinstance(allow_cloud_raw, bool) else (False if local_only else True)
    model = str(item.get("model") or metadata.get("model") or "auto/backlog-burn")
    max_tokens = int(item.get("max_completion_tokens") or item.get("max_tokens") or metadata.get("max_completion_tokens") or 700)
    return BacklogTaskCandidate(
        task_id=task_id or title,
        title=title,
        prompt=prompt,
        model=model,
        priority=priority,
        local_only=local_only,
        allow_cloud=allow_cloud,
        sensitive=sensitive,
        max_completion_tokens=max_tokens,
        metadata={
            **metadata,
            "assistx_source": True,
            "assistx_raw_status": item.get("status"),
            "assistx_queue": item.get("queue"),
        },
    )


def _extract_tasks(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("tasks", "items", "results", "backlog"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _priority(value: str) -> Priority:
    try:
        return Priority(value)
    except ValueError:
        if value in {"low", "deferred", "idle"}:
            return Priority.background
        if value in {"normal", "medium"}:
            return Priority.batch
        return Priority.background
