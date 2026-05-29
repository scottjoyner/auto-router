from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock

from auto_router.models import ModelConfig, ProviderConfig, QuotaEstimate, QuotaSnapshot


@dataclass
class QuotaCounter:
    limit: int | None
    used: int = 0
    reset_at: int | None = None
    window_seconds: int | None = None

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(self.limit - self.used, 0)

    def reset_if_needed(self, now: int) -> None:
        if self.reset_at is not None and now >= self.reset_at:
            self.used = 0
            if self.window_seconds is not None:
                skipped_windows = max((now - self.reset_at) // self.window_seconds, 0)
                self.reset_at = self.reset_at + ((skipped_windows + 1) * self.window_seconds)
            else:
                self.reset_at = None


@dataclass
class InMemoryQuotaManager:
    """Simple quota manager for MVP and unit tests.

    Redis-backed atomic reservations will replace this implementation in phase 2.
    """

    counters: dict[str, QuotaCounter] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def estimate(self, model: ModelConfig, body: dict) -> QuotaEstimate:
        max_tokens = int(body.get("max_tokens") or body.get("max_completion_tokens") or 512)
        rough_input = self._rough_token_count(body)
        dimensions = {"requests": 1, "tokens": rough_input + max_tokens}
        return QuotaEstimate(
            request_units=1,
            input_tokens=rough_input,
            output_tokens=max_tokens,
            total_tokens=rough_input + max_tokens,
            dimensions=dimensions,
        )

    def can_reserve(self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate) -> bool:
        with self.lock:
            return self._can_reserve_unlocked(provider, model, estimate)

    def reserve(self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate) -> bool:
        with self.lock:
            if not self._can_reserve_unlocked(provider, model, estimate):
                return False
            for dimension, amount in self._dimension_amounts(model, estimate).items():
                key = self._key(provider.name, model.alias, dimension)
                self.counters.setdefault(key, self._counter_for(model, dimension)).used += amount
            return True

    def snapshots(self, providers: list[ProviderConfig]) -> list[QuotaSnapshot]:
        results: list[QuotaSnapshot] = []
        with self.lock:
            now = int(time.time())
            for provider in providers:
                for model in provider.models:
                    dimensions: dict[str, dict[str, int | None]] = {}
                    for dimension in self._configured_dimensions(model):
                        key = self._key(provider.name, model.alias, dimension)
                        counter = self.counters.setdefault(key, self._counter_for(model, dimension))
                        counter.reset_if_needed(now)
                        dimensions[dimension] = {
                            "limit": counter.limit,
                            "used": counter.used,
                            "remaining": counter.remaining,
                            "reset_at": counter.reset_at,
                        }
                    results.append(
                        QuotaSnapshot(provider=provider.name, model=model.alias, dimensions=dimensions)
                    )
        return results

    def _can_reserve_unlocked(
        self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate
    ) -> bool:
        now = int(time.time())
        for dimension, amount in self._dimension_amounts(model, estimate).items():
            key = self._key(provider.name, model.alias, dimension)
            existing = self.counters.setdefault(key, self._counter_for(model, dimension))
            existing.reset_if_needed(now)
            if existing.limit is not None and existing.used + amount > existing.limit:
                return False
        return True

    def _rough_token_count(self, body: dict) -> int:
        text = str(body.get("messages") or body.get("input") or body)
        return max(len(text) // 4, 1)

    def _configured_dimensions(self, model: ModelConfig) -> list[str]:
        dimensions = set(model.quota.keys())
        if not dimensions:
            return ["requests", "tokens"]
        return sorted(dimensions)

    def _dimension_amounts(self, model: ModelConfig, estimate: QuotaEstimate) -> dict[str, int]:
        amounts: dict[str, int] = {}
        for dimension in self._configured_dimensions(model):
            if dimension in {"rpm", "rpd"}:
                amounts[dimension] = estimate.request_units
            elif dimension in {"tpm", "tpd", "tph", "tokens"}:
                amounts[dimension] = estimate.total_tokens
            elif dimension in {"neurons", "neurons_d"}:
                amounts[dimension] = estimate.total_tokens
            else:
                amounts[dimension] = estimate.dimensions.get(dimension, estimate.request_units)
        return amounts

    def _counter_for(self, model: ModelConfig, dimension: str) -> QuotaCounter:
        limit = model.quota.get(dimension)
        now = int(time.time())
        if dimension in {"rpm", "tpm"}:
            return QuotaCounter(limit=limit, reset_at=now + 60, window_seconds=60)
        if dimension in {"rpd", "tpd", "neurons", "neurons_d"}:
            return QuotaCounter(limit=limit, reset_at=now + 86400, window_seconds=86400)
        return QuotaCounter(limit=limit)

    def _key(self, provider: str, model: str, dimension: str) -> str:
        return f"{provider}:{model}:{dimension}"
