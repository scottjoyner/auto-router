from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from auto_router.context import ContextService, ServiceStatus


@dataclass
class ServiceProbeResult:
    service_id: str
    name: str
    url: str
    status: ServiceStatus
    checked_at: int
    latency_ms: int | None = None
    status_code: int | None = None
    error: str | None = None
    skipped: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "url": self.url,
            "status": self.status.value,
            "checked_at": self.checked_at,
            "latency_ms": self.latency_ms,
            "status_code": self.status_code,
            "error": self.error,
            "skipped": self.skipped,
            "reason": self.reason,
        }


@dataclass
class ServiceStatusCache:
    results: dict[str, ServiceProbeResult] = field(default_factory=dict)

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in sorted(self.results.values(), key=lambda result: result.name.lower())]

    def update(self, result: ServiceProbeResult) -> None:
        self.results[result.service_id] = result

    def merge_status(self, service: ContextService) -> ContextService:
        result = self.results.get(service.service_id)
        if not result:
            return service
        return service.model_copy(update={"status": result.status})


def is_private_or_local_service(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False
    host_lower = host.lower()
    if host_lower in {"localhost", "127.0.0.1", "::1"}:
        return True
    if host_lower.endswith(".lan") or ".local" in host_lower:
        return True
    if "." not in host_lower:
        return True
    try:
        ip = ipaddress.ip_address(host_lower)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


async def probe_service(
    service: ContextService,
    timeout_seconds: float = 2.0,
    allow_external: bool = False,
) -> ServiceProbeResult:
    checked_at = int(time.time())
    target_url = service.health_url or service.url
    if service.status == ServiceStatus.blocked:
        return ServiceProbeResult(
            service_id=service.service_id,
            name=service.name,
            url=target_url,
            status=ServiceStatus.blocked,
            checked_at=checked_at,
            skipped=True,
            reason="service is blocked in context",
        )
    if not allow_external and not is_private_or_local_service(target_url):
        return ServiceProbeResult(
            service_id=service.service_id,
            name=service.name,
            url=target_url,
            status=ServiceStatus.unknown,
            checked_at=checked_at,
            skipped=True,
            reason="external probing disabled",
        )

    parsed = urlparse(target_url)
    if parsed.scheme in {"http", "https"}:
        return await _probe_http(service, target_url, checked_at, timeout_seconds)
    if parsed.scheme in {"bolt", "redis", "tcp"}:
        return await _probe_tcp(service, parsed, checked_at, timeout_seconds)

    return ServiceProbeResult(
        service_id=service.service_id,
        name=service.name,
        url=target_url,
        status=ServiceStatus.unknown,
        checked_at=checked_at,
        skipped=True,
        reason=f"unsupported scheme: {parsed.scheme or 'missing'}",
    )


async def scan_services(
    services: list[ContextService],
    timeout_seconds: float = 2.0,
    allow_external: bool = False,
    limit: int | None = None,
) -> list[ServiceProbeResult]:
    selected = services[:limit] if limit else services
    return await asyncio.gather(
        *(probe_service(service, timeout_seconds=timeout_seconds, allow_external=allow_external) for service in selected)
    )


async def _probe_http(
    service: ContextService,
    target_url: str,
    checked_at: int,
    timeout_seconds: float,
) -> ServiceProbeResult:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            response = await client.get(target_url)
        latency_ms = int((time.perf_counter() - started) * 1000)
        status = ServiceStatus.online if response.status_code < 500 else ServiceStatus.degraded
        return ServiceProbeResult(
            service_id=service.service_id,
            name=service.name,
            url=target_url,
            status=status,
            checked_at=checked_at,
            latency_ms=latency_ms,
            status_code=response.status_code,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ServiceProbeResult(
            service_id=service.service_id,
            name=service.name,
            url=target_url,
            status=ServiceStatus.offline,
            checked_at=checked_at,
            latency_ms=latency_ms,
            error=str(exc)[:500],
        )


async def _probe_tcp(
    service: ContextService,
    parsed: Any,
    checked_at: int,
    timeout_seconds: float,
) -> ServiceProbeResult:
    started = time.perf_counter()
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return ServiceProbeResult(
            service_id=service.service_id,
            name=service.name,
            url=parsed.geturl(),
            status=ServiceStatus.unknown,
            checked_at=checked_at,
            skipped=True,
            reason="missing host or port",
        )

    def connect() -> None:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return None

    try:
        await asyncio.to_thread(connect)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ServiceProbeResult(
            service_id=service.service_id,
            name=service.name,
            url=parsed.geturl(),
            status=ServiceStatus.online,
            checked_at=checked_at,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ServiceProbeResult(
            service_id=service.service_id,
            name=service.name,
            url=parsed.geturl(),
            status=ServiceStatus.offline,
            checked_at=checked_at,
            latency_ms=latency_ms,
            error=str(exc)[:500],
        )
