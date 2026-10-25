"""Research-only resource-local queued-byte barrier, NEVER live provider IO.

Proof target: a locally acknowledged revoke and queued synthetic chunks can
be serialized with delivery in ONE Python process. This is not network/model
side effect fencing. Concurrent remote revocation and cloned authority state
remain unfenced. Caller-owned witness callbacks are not a trusted broker.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Callable


class LocalDeliveryDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalSnapshot:
    epoch: int
    revision: int
    active: bool


class LocalDeliveryBarrier:
    """Synchronous fake sink only. A local lock is NOT distributed custody."""

    def __init__(
        self, *,
        epoch: int,
        read_current: Callable[[], LocalSnapshot],
        fake_deliver: Callable[[bytes], None],
        fake_abort: Callable[[], None],
        max_queue: int = 4,
    ):
        if type(epoch) is not int or epoch <= 0:
            raise ValueError("positive epoch required")
        if type(max_queue) is not int or not 1 <= max_queue <= 16:
            raise ValueError("bounded in-memory queue required")
        self._epoch = epoch
        self._read_current = read_current
        self._fake_deliver = fake_deliver
        self._fake_abort = fake_abort
        self._max_queue = max_queue
        self._lock = RLock()
        self._queue: deque[bytes] = deque()
        self._closed = False
        self._abort_called = False
        self._highwater = 0
        self._delivered = 0
        self._possibly_accepted = False

    def _stop(self) -> None:
        self._closed = True
        self._queue.clear()
        if not self._abort_called:
            self._abort_called = True
            # Synthetic callback, not a real provider process abort.
            try:
                self._fake_abort()
            except Exception:
                self._possibly_accepted = True

    def stage(self, chunk: bytes) -> None:
        """Buffer-only: MUST NOT release any bytes or claim provider access."""
        with self._lock:
            if self._closed:
                raise LocalDeliveryDenied("local barrier closed")
            if type(chunk) is not bytes or not 0 < len(chunk) <= 1024:
                self._stop()
                raise LocalDeliveryDenied("invalid synthetic bytes")
            if len(self._queue) >= self._max_queue:
                self._stop()
                raise LocalDeliveryDenied("synthetic buffer limit exceeded")
            self._queue.append(chunk)

    def revoke_local(self, revision: int) -> None:
        """Ack only after acquiring SAME local lock as fake sink delivery."""
        with self._lock:
            if type(revision) is not int or revision <= 0:
                self._stop()
                raise LocalDeliveryDenied("invalid revocation revision")
            self._highwater = max(self._highwater, revision)
            self._stop()

    def deliver_one(self) -> bool:
        """At most one buffered chunk. Not atomic with REMOTE revocation."""
        with self._lock:
            if self._closed:
                raise LocalDeliveryDenied("local barrier closed")
            if not self._queue:
                return False
            sink_attempted = False
            try:
                current = self._read_current()  # never use a cached successful answer
                if (
                    type(current) is not LocalSnapshot
                    or type(current.epoch) is not int
                    or type(current.revision) is not int
                    or type(current.active) is not bool
                    or current.epoch != self._epoch
                    or current.revision <= 0
                    or current.revision < self._highwater
                    or not current.active
                ):
                    raise LocalDeliveryDenied("authority unavailable, stale or revoked")
                self._highwater = current.revision
                chunk = self._queue[0]
                sink_attempted = True
                self._fake_deliver(chunk)  # strictly synthetic, under local lock
                self._queue.popleft()
                self._delivered += 1
                return True
            except Exception as exc:
                # A fake transport may have accepted bytes before it raised.
                # A witness denial *before* the fake sink is not accepted.
                # Never retry output whose delivery was attempted and failed.
                if sink_attempted:
                    self._possibly_accepted = True
                self._stop()
                raise LocalDeliveryDenied("fake output blocked; outcome uncertain") from exc

    @property
    def evidence(self) -> dict:
        with self._lock:
            return {
                "queued": len(self._queue),
                "fake_deliveries": self._delivered,
                "local_revision_highwater": self._highwater,
                "closed": self._closed,
                "synthetic_abort_called": self._abort_called,
                "possibly_accepted": self._possibly_accepted,
                "provider_calls": 0,
                "sql_executed": False,
                "provider_execution_authorized": False,
                "remote_revocation_atomicity_proven": False,
                "noncopyable_highwater_proven": False,
            }
