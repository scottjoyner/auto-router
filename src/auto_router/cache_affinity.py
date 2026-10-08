"""Cache-affinity identity/scoring primitives for experimental routing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class CacheIdentity:
    model_hash: str
    quant: str
    context_size: int
    runtime_id: str
    session_id: str
    prefix_fingerprint: str

    @classmethod
    def from_request(
        cls,
        *,
        model_hash: str,
        quant: str,
        context_size: int,
        runtime_id: str,
        session_id: str,
        stable_prefix: str,
    ) -> "CacheIdentity":
        if context_size <= 0:
            raise ValueError("context_size must be positive")
        values = [model_hash, quant, runtime_id, session_id]
        if any(not value.strip() for value in values):
            raise ValueError("cache identity strings must be non-empty")
        prefix_fingerprint = hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest()
        return cls(
            model_hash=model_hash,
            quant=quant,
            context_size=context_size,
            runtime_id=runtime_id,
            session_id=session_id,
            prefix_fingerprint=prefix_fingerprint,
        )


def affinity_score(request: CacheIdentity, candidate: CacheIdentity) -> int:
    """Score only compatible identities; incompatible model/runtime state gets zero."""
    hard_match = (
        request.model_hash == candidate.model_hash
        and request.quant == candidate.quant
        and request.context_size == candidate.context_size
        and request.runtime_id == candidate.runtime_id
    )
    if not hard_match:
        return 0
    score = 1
    if request.session_id == candidate.session_id:
        score += 2
    if request.prefix_fingerprint == candidate.prefix_fingerprint:
        score += 4
    return score
