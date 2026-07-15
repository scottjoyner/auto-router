from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class CircuitState:
    owner: str
    failures: int = 0
    opened_until: int | None = None
    last_error: str | None = None

    @property
    def open(self) -> bool:
        return self.opened_until is not None and int(time.time()) < self.opened_until


@dataclass
class CircuitBreakerManager:
    failure_threshold: int = 3
    cooldown_seconds: int = 60
    states: dict[str, CircuitState] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def allowed(self, owner: str) -> bool:
        with self.lock:
            state = self.states.get(owner)
            if state is None:
                return True
            if state.open:
                return False
            if state.opened_until is not None and int(time.time()) >= state.opened_until:
                state.failures = 0
                state.opened_until = None
                state.last_error = None
            return True

    def record_success(self, owner: str) -> None:
        with self.lock:
            self.states[owner] = CircuitState(owner=owner)

    def record_failure(self, owner: str, error: str, retry_after: int | None = None) -> None:
        with self.lock:
            state = self.states.setdefault(owner, CircuitState(owner=owner))
            state.failures += 1
            state.last_error = error
            if state.failures >= self.failure_threshold:
                state.opened_until = int(time.time()) + (retry_after or self.cooldown_seconds)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [
                {
                    "owner": state.owner,
                    "failures": state.failures,
                    "opened_until": state.opened_until,
                    "open": state.open,
                    "last_error": state.last_error,
                }
                for state in self.states.values()
            ]

    def reset(self, owner: str | None = None) -> int:
        """Clear breaker state. If ``owner`` is given, reset only that owner;
        otherwise reset all breakers. Returns the number of breakers cleared.
        This lets an operator recover a tripped breaker immediately instead of
        waiting out the cooldown."""
        with self.lock:
            if owner is None:
                n = len(self.states)
                self.states.clear()
                return n
            if owner in self.states:
                del self.states[owner]
                return 1
            return 0
