from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from auto_router.models import ProviderConfig


@dataclass
class LiveModelSnapshot:
    provider: str
    ok: bool
    fetched_at: int
    expires_at: int
    models: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    latency_ms: int | None = None
    drift: bool = False
    signature: str | None = None
    previous_signature: str | None = None
    changed_models: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return int(time.time()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "fetched_at": self.fetched_at,
            "expires_at": self.expires_at,
            "stale": self.stale,
            "models": self.models,
            "model_count": len(self.models),
            "error": self.error,
            "latency_ms": self.latency_ms,
            "drift": self.drift,
            "signature": self.signature,
            "previous_signature": self.previous_signature,
            "changed_models": self.changed_models,
        }


class LiveModelCache:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = max(int(ttl_seconds), 60)
        self._snapshots: dict[str, LiveModelSnapshot] = {}

    def snapshot(self) -> list[dict[str, Any]]:
        records = sorted(self._snapshots.values(), key=lambda item: item.provider)
        return [record.to_dict() for record in records]

    def get(self, provider_name: str) -> LiveModelSnapshot | None:
        return self._snapshots.get(provider_name)

    def put(self, snapshot: LiveModelSnapshot) -> None:
        self._snapshots[snapshot.provider] = snapshot

    async def get_or_refresh(self, provider: ProviderConfig, fetcher: Any, force: bool = False) -> LiveModelSnapshot:
        current = self._snapshots.get(provider.name)
        if current is not None and not force and not current.stale:
            return current
        return await self.refresh_provider(provider, fetcher)

    async def refresh_provider(self, provider: ProviderConfig, fetcher: Any) -> LiveModelSnapshot:
        now = int(time.time())
        try:
            result = await fetcher(provider)
            snapshot = LiveModelSnapshot(
                provider=provider.name,
                ok=True,
                fetched_at=now,
                expires_at=now + self.ttl_seconds,
                models=list(result or []),
            )
        except Exception as exc:
            snapshot = LiveModelSnapshot(
                provider=provider.name,
                ok=False,
                fetched_at=now,
                expires_at=now + min(self.ttl_seconds, 300),
                error=str(exc),
            )
        self.put(snapshot)
        return snapshot
