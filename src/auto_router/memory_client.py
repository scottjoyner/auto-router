from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx

from auto_router.memory_models import MemoryContext, MemoryIngestRequest, MemoryQuery
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

    async def ingest(self, request: MemoryIngestRequest) -> dict[str, object]:
        cached = self.store.ingest(request)
        if not self.base_url:
            return {"accepted": cached, "backend": "sqlite", "forwarded": False}
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

    async def assemble(self, query: MemoryQuery) -> MemoryContext:
        remote_allowed = self.base_url and (
            query.privacy_class != "local_only" or self._is_private_url(self.base_url)
        )
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
                return context
            except (httpx.HTTPError, ValueError) as exc:
                context = self.store.query(query)
                context.warnings.append(f"Remote memory lookup failed: {exc}")
                return context
        context = self.store.query(query)
        if self.base_url and not remote_allowed:
            context.warnings.append(
                "Remote memory lookup skipped because local_only context cannot be sent "
                "to a non-private service URL."
            )
        return context

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
