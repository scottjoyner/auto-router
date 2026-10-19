"""Research-only per-chunk effect fence for a synthetic xwing stream.

An accepted chunk is a PostgreSQL transaction ACK, not a proof that the
client received it. Never wire the test transport into production serving.
No network retry/connection fallback is permitted after authorization loss.
"""
from __future__ import annotations

import hashlib
from typing import Callable


class WorkerEffectDenied(RuntimeError):
    pass


class GuardedSyntheticStream:
    def __init__(
        self, *,
        accept_chunk: Callable[[int, str], bool],
        cancel_model: Callable[[], None],
    ):
        self.accept_chunk=accept_chunk
        self.cancel_model=cancel_model
        self.cancelled=False
        self.accepted=0

    def stop(self) -> None:
        if not self.cancelled:
            self.cancelled=True
            self.cancel_model()

    def emit(self, chunk: bytes) -> bytes:
        """No bytes exposed unless a current-epoch DB effect ACK commits."""
        if self.cancelled:
            raise WorkerEffectDenied("stream already cancelled")
        if type(chunk) is not bytes or not chunk:
            self.stop()
            raise WorkerEffectDenied("invalid synthetic output")
        sequence=self.accepted+1
        digest=hashlib.sha256(chunk).hexdigest()
        try:
            permitted=self.accept_chunk(sequence,digest)
            if permitted is not True:
                raise WorkerEffectDenied("fenced chunk not accepted")
        except Exception as exc:
            self.stop()
            raise WorkerEffectDenied("output blocked by authority") from exc
        self.accepted=sequence
        return chunk
