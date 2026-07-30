from __future__ import annotations

import asyncio
import ipaddress
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from auto_router.models import ProviderCandidate, ProviderConfig
from auto_router.providers import ProviderError

Probe = Callable[[str], Awaitable[bool]]
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True)
class AccessPathChoice:
    runtime_instance_id: str
    base_url: str
    transport: str
    selected_at: float
    expires_at: float


class RuntimeAccessPathSelector:
    """Choose among AssistX-approved paths without discovering new providers.

    Access paths are ordered. The expected order is same-LAN first and Tailscale
    second. All paths refer to the same physical runtime and therefore share one
    admission gate and one capacity record.
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        *,
        cache_ttl_seconds: float = 15.0,
        probe_timeout_seconds: float = 2.0,
        probe: Probe | None = None,
    ) -> None:
        self.cache_ttl_seconds = max(float(cache_ttl_seconds), 1.0)
        self.probe_timeout_seconds = max(float(probe_timeout_seconds), 0.2)
        self._probe_override = probe
        self._paths: dict[str, list[str]] = {}
        self._provider_runtime_keys: dict[str, str] = {}
        self._choices: dict[str, AccessPathChoice] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._probe_failures: dict[str, int] = {}

        for provider in providers:
            runtime_id = str(provider.runtime_instance_id or "").strip()
            if not runtime_id:
                runtime_id = f"unresolved:{provider.name}"
            self._provider_runtime_keys[provider.name] = runtime_id
            self._locks.setdefault(runtime_id, asyncio.Lock())
            self._paths[runtime_id] = self._ordered_unique_urls(provider)

    @staticmethod
    def _ordered_unique_urls(provider: ProviderConfig) -> list[str]:
        ordered: list[str] = []
        for value in [*provider.access_urls, provider.base_url]:
            normalized = str(value or "").strip().rstrip("/")
            if normalized and normalized not in ordered:
                ordered.append(normalized)
        return ordered

    async def select(self, candidate: ProviderCandidate) -> AccessPathChoice:
        runtime_id = self._provider_runtime_keys.get(
            candidate.provider.name,
            f"unresolved:{candidate.provider.name}",
        )
        now = time.monotonic()
        cached = self._choices.get(runtime_id)
        if cached is not None and cached.expires_at > now:
            return cached

        lock = self._locks.setdefault(runtime_id, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._choices.get(runtime_id)
            if cached is not None and cached.expires_at > now:
                return cached

            urls = self._paths.get(runtime_id) or self._ordered_unique_urls(candidate.provider)
            for base_url in urls:
                if await self._probe(base_url):
                    choice = AccessPathChoice(
                        runtime_instance_id=runtime_id,
                        base_url=base_url,
                        transport=classify_access_transport(base_url),
                        selected_at=now,
                        expires_at=now + self.cache_ttl_seconds,
                    )
                    self._choices[runtime_id] = choice
                    return choice
                self._probe_failures[base_url] = self._probe_failures.get(base_url, 0) + 1

        raise ProviderError(
            f"no approved access path is reachable for runtime {runtime_id}",
            status_code=503,
            retryable=True,
        )

    async def _probe(self, base_url: str) -> bool:
        if self._probe_override is not None:
            return bool(await self._probe_override(base_url))
        url = f"{base_url.rstrip('/')}/models"
        timeout = httpx.Timeout(
            connect=self.probe_timeout_seconds,
            read=self.probe_timeout_seconds,
            write=self.probe_timeout_seconds,
            pool=self.probe_timeout_seconds,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url)
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        result: list[dict[str, Any]] = []
        for runtime_id in sorted(self._paths):
            choice = self._choices.get(runtime_id)
            result.append(
                {
                    "runtime_instance_id": runtime_id,
                    "approved_access_urls": list(self._paths[runtime_id]),
                    "selected_access_url": choice.base_url if choice else None,
                    "selected_transport": choice.transport if choice else None,
                    "selection_fresh": bool(choice and choice.expires_at > now),
                    "probe_failures": {
                        url: self._probe_failures.get(url, 0)
                        for url in self._paths[runtime_id]
                    },
                }
            )
        return result


def classify_access_transport(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if host.endswith(".ts.net"):
        return "tailscale"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host in {"host.docker.internal", "gateway.docker.internal"}:
            return "host_gateway"
        if host.endswith((".lan", ".local")):
            return "lan"
        return "local_dns"
    if address in _TAILSCALE_CGNAT:
        return "tailscale"
    if address.is_private or address.is_link_local:
        return "lan"
    if address.is_loopback:
        return "loopback"
    return "unknown"
