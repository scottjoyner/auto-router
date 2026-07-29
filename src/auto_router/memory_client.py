from __future__ import annotations

import ipaddress
import time
from collections import Counter
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from auto_router.memory_models import (
    MemoryContext,
    MemoryIngestRequest,
    MemoryLifecycleRequest,
    MemoryOutcomeRequest,
    MemoryQuery,
)
from auto_router.memory_store import MemoryStore


class MemoryClient:
    """Extraction seam for the future auto-memory service.

    Remote responses are authoritative. The local store is an idempotent cache
    and degraded-mode retrieval path, never the canonical graph authority.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 5.0,
    ):
        self.store = store
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._metrics: Counter[str] = Counter()
        self._retrieval_ms_total = 0.0

    async def ingest(self, request: MemoryIngestRequest) -> dict[str, object]:
        cached = self.store.ingest(request)
        self._metrics["ingest_events"] += 1
        if not self.base_url:
            return {"accepted": cached, "backend": "sqlite", "forwarded": False}
        if not self._event_remote_allowed(request):
            return {
                "accepted": cached,
                "backend": "sqlite",
                "forwarded": False,
                "warning": "Memory event was not sent to a non-private service URL.",
            }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/v1/memory/events",
                    json=request.model_dump(mode="json"),
                )
                response.raise_for_status()
            return {"accepted": cached, "backend": "assistx-neo4j", "forwarded": True}
        except httpx.HTTPError as exc:
            return {
                "accepted": cached,
                "backend": "sqlite",
                "forwarded": False,
                "warning": f"Memory event cached locally after remote failure: {exc}",
            }

    async def record_lifecycle(self, request: MemoryLifecycleRequest) -> dict[str, object]:
        return await self._record_event(
            request,
            local=self.store.record_lifecycle,
            remote_path="/v1/memory/lifecycle",
        )

    async def record_outcome(self, request: MemoryOutcomeRequest) -> dict[str, object]:
        return await self._record_event(
            request,
            local=self.store.record_outcome,
            remote_path="/v1/memory/outcomes",
        )

    async def record_agent_job_outcome(
        self,
        request: Any,
        result: Any,
        latency_ms: int,
        error: str | None,
    ) -> None:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        fleet_memory = metadata.get("fleet_memory")
        memory_ids = fleet_memory.get("memory_ids", []) if isinstance(fleet_memory, dict) else []
        usage = result.usage if result is not None and isinstance(result.usage, dict) else {}
        await self.record_outcome(
            MemoryOutcomeRequest(
                event_id=f"agent-job:{request.job_id}:completion",
                source="auto-router-agent-jobs",
                task_id=request.job_id,
                repository=metadata.get("repository") or metadata.get("repo"),
                commit_sha=metadata.get("commit_sha"),
                success=result is not None and result.status == "succeeded",
                validation_passed=(
                    metadata.get("validation_passed")
                    if isinstance(metadata.get("validation_passed"), bool)
                    else None
                ),
                provider=result.worker_name if result is not None else None,
                model=metadata.get("model"),
                node_id=metadata.get("node_id"),
                latency_ms=latency_ms,
                tokens_per_second=usage.get("tokens_per_second"),
                error_signature=error
                or (result.stderr[-500:] if result is not None and result.stderr else None),
                retry_path=[str(item) for item in metadata.get("retry_path", []) if str(item)],
                memory_ids=[str(item) for item in memory_ids if str(item)],
                metadata={"status": result.status if result is not None else "failed"},
            )
        )

    async def _record_event(
        self,
        request: Any,
        *,
        local: Callable[[Any], bool],
        remote_path: str,
    ) -> dict[str, object]:
        cached = local(request)
        self._metrics[
            "outcome_events" if remote_path.endswith("outcomes") else "lifecycle_events"
        ] += 1
        if not self.base_url:
            return {"accepted": cached, "backend": "sqlite", "forwarded": False}
        if not self._event_remote_allowed(request):
            return {
                "accepted": cached,
                "backend": "sqlite",
                "forwarded": False,
                "warning": "Memory event was not sent to a non-private service URL.",
            }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}{remote_path}",
                    json=request.model_dump(mode="json"),
                )
                response.raise_for_status()
            return {"accepted": cached, "backend": "assistx-neo4j", "forwarded": True}
        except httpx.HTTPError as exc:
            return {
                "accepted": cached,
                "backend": "sqlite",
                "forwarded": False,
                "warning": f"Memory event cached locally after remote failure: {exc}",
            }

    async def assemble(self, query: MemoryQuery) -> MemoryContext:
        remote_allowed = self.base_url and (
            query.privacy_class != "local_only" or self._is_private_url(self.base_url)
        )
        started = time.perf_counter()
        self._metrics["retrievals"] += 1
        if remote_allowed:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}/v1/memory/context",
                        json=query.model_dump(mode="json"),
                    )
                    response.raise_for_status()
                context = MemoryContext.model_validate(response.json())
                context.backend = "assistx-neo4j"
                context.degraded = False
                context.retrieval_ms = round((time.perf_counter() - started) * 1000, 3)
                self._observe_context(context)
                return context
            except (httpx.HTTPError, ValueError) as exc:
                self._metrics["remote_failures"] += 1
                context = self.store.query(query)
                context.warnings.append(f"Remote memory lookup failed: {exc}")
                self._observe_context(context)
                return context
        context = self.store.query(query)
        self._metrics["local_fallbacks"] += 1
        if self.base_url and not remote_allowed:
            context.warnings.append(
                "Remote memory lookup skipped because local_only context cannot be sent "
                "to a non-private service URL."
            )
        self._observe_context(context)
        return context

    def metrics(self) -> dict[str, float | int]:
        retrievals = self._metrics["retrievals"]
        return {
            **dict(self._metrics),
            "avg_retrieval_ms": (self._retrieval_ms_total / retrievals if retrievals else 0.0),
        }

    def _observe_context(self, context: MemoryContext) -> None:
        self._retrieval_ms_total += context.retrieval_ms
        self._metrics["matches_returned"] += len(context.matches)
        self._metrics["context_tokens"] += context.estimated_tokens

    def _event_remote_allowed(self, request: Any) -> bool:
        privacy_class = str(getattr(request, "privacy_class", "local_only"))
        return privacy_class != "local_only" or self._is_private_url(self.base_url)

    @staticmethod
    def _is_private_url(url: str) -> bool:
        hostname = (urlparse(url).hostname or "").lower()
        if not hostname:
            return False
        if hostname in {"localhost", "host.docker.internal"}:
            return True
        if "." not in hostname or hostname.endswith((".lan", ".local", ".ts.net")):
            return True
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return False
        return address.is_private or address.is_loopback
