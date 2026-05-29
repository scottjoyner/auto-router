from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from auto_router.models import ModelConfig, ProviderConfig, QuotaEstimate, QuotaSnapshot

try:  # pragma: no cover - optional dependency
    import redis as redis_lib
    from redis.exceptions import WatchError
except Exception:  # pragma: no cover - optional dependency
    redis_lib = None

    class WatchError(Exception):
        pass


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

    def release(self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate) -> None:
        """Refund a reservation after a failed dispatch.

        This keeps failed upstream attempts from burning quota permanently while still
        preserving the pre-dispatch reservation model that prevents oversubscription.
        """
        with self.lock:
            for dimension, amount in self._dimension_amounts(model, estimate).items():
                key = self._key(provider.name, model.alias, dimension)
                counter = self.counters.setdefault(key, self._counter_for(model, dimension))
                counter.used = max(counter.used - amount, 0)

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


class RedisQuotaManager:
    """Redis-backed quota manager for atomic reservations.

    The manager stores counters per provider/model/dimension and uses Redis WATCH/MULTI
    so multi-dimension reservations are all-or-nothing.
    """

    def __init__(self, redis_url: str, key_prefix: str = "auto-router:quota"):
        if redis_lib is None:  # pragma: no cover - import-time environment dependent
            raise RuntimeError("redis package is not installed")
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self.client.ping()

    def estimate(self, model: ModelConfig, body: dict) -> QuotaEstimate:
        return InMemoryQuotaManager().estimate(model, body)

    def can_reserve(self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate) -> bool:
        with self.client.pipeline() as pipe:
            return self._attempt_reservation(pipe, provider, model, estimate, commit=False)

    def reserve(self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate) -> bool:
        with self.client.pipeline() as pipe:
            return self._attempt_reservation(pipe, provider, model, estimate, commit=True)

    def release(self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate) -> None:
        with self.client.pipeline() as pipe:
            while True:
                try:
                    keys = self._dimension_keys(provider, model, estimate)
                    if keys:
                        pipe.watch(*keys)
                    states = self._current_states(pipe, provider, model, estimate)
                    now = int(time.time())
                    updates: list[tuple[str, dict[str, int | None]]] = []
                    for dimension, amount, key, state in states:
                        state = self._refresh_state(model, dimension, state, now)
                        state["used"] = max(int(state.get("used") or 0) - amount, 0)
                        updates.append((key, state))
                    pipe.multi()
                    for key, state in updates:
                        self._store_state(pipe, key, state)
                    pipe.execute()
                    return
                except WatchError:
                    continue
                finally:
                    try:
                        pipe.reset()
                    except Exception:  # pragma: no cover - defensive
                        pass

    def snapshots(self, providers: list[ProviderConfig]) -> list[QuotaSnapshot]:
        results: list[QuotaSnapshot] = []
        for provider in providers:
            for model in provider.models:
                dimensions: dict[str, dict[str, int | None]] = {}
                for dimension in self._configured_dimensions(model):
                    key = self._key(provider.name, model.alias, dimension)
                    raw = self.client.hgetall(key)
                    state = self._refresh_state(model, dimension, self._parse_state(model, dimension, raw), int(time.time()))
                    dimensions[dimension] = {
                        "limit": state["limit"],
                        "used": state["used"],
                        "remaining": None if state["limit"] is None else max(state["limit"] - int(state["used"]), 0),
                        "reset_at": state["reset_at"],
                    }
                results.append(QuotaSnapshot(provider=provider.name, model=model.alias, dimensions=dimensions))
        return results

    def get_circuit_state(self, provider_name: str) -> dict[str, Any]:
        key = f"{self.key_prefix}:circuit:{provider_name}"
        raw = self.client.hgetall(key)
        if not raw:
            return {"state": "closed", "last_error": None}
        opened_until = raw.get("opened_until")
        if opened_until and int(opened_until) < int(time.time()):
            return {"state": "closed", "last_error": None} # Circuit has expired
        return {
            "state": raw.get("state", "closed"),
            "last_error": raw.get("last_error"),
            "opened_until": raw.get("opened_until"),
        }

    def record_failure(self, provider_name: str, error: str):
        key = f"{self.key_prefix}:circuit:{provider_name}"
        # Simple circuit breaker: open for 5 minutes on error
        opened_until = int(time.time()) + 300
        self.client.hset(key, mapping={
            "state": "open",
            "last_error": error[:100],
            "opened_until": opened_until
        })
        self.client.expireat(key, opened_until)

    def _attempt_reservation(
        self,
        pipe: Any,
        provider: ProviderConfig,
        model: ModelConfig,
        estimate: QuotaEstimate,
        *,
        commit: bool,
    ) -> bool:
        while True:
            try:
                keys = self._dimension_keys(provider, model, estimate)
                if keys:
                    pipe.watch(*keys)
                states = self._current_states(pipe, provider, model, estimate)
                now = int(time.time())
                updates: list[tuple[str, dict[str, int | None]]] = []
                for dimension, amount, key, state in states:
                    state = self._refresh_state(model, dimension, state, now)
                    if state["limit"] is not None and int(state["used"]) + amount > int(state["limit"]):
                        return False
                    state["used"] = int(state["used"]) + amount
                    updates.append((key, state))
                if commit:
                    pipe.multi()
                    for key, state in updates:
                        self._store_state(pipe, key, state)
                    pipe.execute()
                return True
            except WatchError:
                continue
            finally:
                try:
                    pipe.reset()
                except Exception:  # pragma: no cover - defensive
                    pass

    def _current_states(
        self,
        pipe: Any,
        provider: ProviderConfig,
        model: ModelConfig,
        estimate: QuotaEstimate,
    ) -> list[tuple[str, int, str, dict[str, int | None]]]:
        states: list[tuple[str, int, str, dict[str, int | None]]] = []
        for dimension, amount in self._dimension_amounts(model, estimate).items():
            key = self._key(provider.name, model.alias, dimension)
            raw = pipe.hgetall(key)
            state = self._parse_state(model, dimension, raw)
            states.append((dimension, amount, key, state))
        return states

    def _parse_state(self, model: ModelConfig, dimension: str, raw: dict[str, str]) -> dict[str, int | None]:
        limit = raw.get("limit")
        used = raw.get("used")
        reset_at = raw.get("reset_at")
        return {
            "limit": int(limit) if limit not in {None, "", "None"} else model.quota.get(dimension),
            "used": int(used) if used not in {None, "", "None"} else 0,
            "reset_at": int(reset_at) if reset_at not in {None, "", "None"} else self._reset_at_for(dimension),
        }

    def _refresh_state(self, model: ModelConfig, dimension: str, state: dict[str, int | None], now: int) -> dict[str, int | None]:
        reset_at = state.get("reset_at")
        if reset_at is not None and now >= int(reset_at):
            state["used"] = 0
            state["reset_at"] = self._next_reset(dimension, now)
        if state.get("limit") is None:
            state["limit"] = model.quota.get(dimension)
        return state

    def _store_state(self, pipe: Any, key: str, state: dict[str, int | None]) -> None:
        mapping = {
            "used": int(state.get("used") or 0),
        }
        if state.get("limit") is not None:
            mapping["limit"] = int(state["limit"])
        if state.get("reset_at") is not None:
            mapping["reset_at"] = int(state["reset_at"])
        pipe.hset(key, mapping=mapping)
        reset_at = state.get("reset_at")
        if reset_at is not None:
            pipe.expireat(key, int(reset_at) + 60)

    def _configured_dimensions(self, model: ModelConfig) -> list[str]:
        dimensions = set(model.quota.keys())
        if not dimensions:
            return ["requests", "tokens"]
        return sorted(dimensions)

    def _dimension_amounts(self, model: ModelConfig, estimate: QuotaEstimate) -> dict[str, int]:
        amounts: dict[str, int] = {}
        for dimension in self._configured_dimensions(model):
            if dimension in {"rpm", "rpd", "conc"}:
                amounts[dimension] = estimate.request_units
            elif dimension in {"tpm", "tpd", "tph", "tpmth", "tokens"}:
                amounts[dimension] = estimate.total_tokens
            elif dimension in {"neurons", "neurons_d", "npd"}:
                amounts[dimension] = estimate.total_tokens
            else:
                amounts[dimension] = estimate.dimensions.get(dimension, estimate.request_units)
        return amounts

    def _reset_at_for(self, dimension: str) -> int | None:
        now = int(time.time())
        if dimension in {"rpm", "tpm"}:
            return now + 60
        if dimension in {"tph"}:
            return now + 3600
        if dimension in {"rpd", "tpd", "tpmth", "neurons", "neurons_d", "npd"}:
            return now + 86400
        return None

    def _next_reset(self, dimension: str, now: int) -> int | None:
        reset = self._reset_at_for(dimension)
        if reset is None:
            return None
        previous_window = max(reset - now, 1)
        return now + previous_window

    def _key(self, provider: str, model: str, dimension: str) -> str:
        return f"{self.key_prefix}:{provider}:{model}:{dimension}"

    def _dimension_keys(self, provider: ProviderConfig, model: ModelConfig, estimate: QuotaEstimate) -> list[str]:
        return [self._key(provider.name, model.alias, dimension) for dimension in self._dimension_amounts(model, estimate)]


def build_quota_manager(redis_url: str | None = None):
    if redis_url and redis_lib is not None:
        try:
            return RedisQuotaManager(redis_url)
        except Exception:
            return InMemoryQuotaManager()
    return InMemoryQuotaManager()
