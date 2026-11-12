"""Typed runtime telemetry contract for cache-affinity shadow evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RuntimeCacheIdentityTelemetry:
    model_hash: str
    quant: str
    context_size: int
    runtime_id: str
    session_id: str
    stable_prefix: str

    def validate(self) -> None:
        if not self.model_hash.strip():
            raise ValueError("model_hash is required")
        if not self.quant.strip():
            raise ValueError("quant is required")
        if self.context_size <= 0:
            raise ValueError("context_size must be positive")
        if not self.runtime_id.strip():
            raise ValueError("runtime_id is required")
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if not self.stable_prefix:
            raise ValueError("stable_prefix is required")

    @property
    def stable_prefix_fingerprint(self) -> str:
        return hashlib.sha256(self.stable_prefix.encode("utf-8")).hexdigest()

    def public_payload(self) -> dict[str, object]:
        """Return the route-shadow payload without exposing the raw stable prefix."""
        self.validate()
        return {
            "model_hash": self.model_hash,
            "quant": self.quant,
            "context_size": self.context_size,
            "runtime_id": self.runtime_id,
            "session_id": self.session_id,
            "stable_prefix": self.stable_prefix,
            "stable_prefix_fingerprint": self.stable_prefix_fingerprint,
        }


@dataclass(frozen=True)
class CandidateCacheTelemetry:
    candidate_id: str
    eligible: bool
    cache_identity: RuntimeCacheIdentityTelemetry
    ttft_ms: float | None = None
    prefill_ms: float | None = None
    cache_hit: bool | None = None

    def validate(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id is required")
        self.cache_identity.validate()
        for name, value in (("ttft_ms", self.ttft_ms), ("prefill_ms", self.prefill_ms)):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class CacheAffinityTelemetryEnvelope:
    request: RuntimeCacheIdentityTelemetry
    candidates: tuple[CandidateCacheTelemetry, ...]
    trace_id: str | None = None
    correlation_id: str | None = None

    def validate(self) -> None:
        self.request.validate()
        if not self.candidates:
            raise ValueError("at least one candidate is required")
        ids: set[str] = set()
        for candidate in self.candidates:
            candidate.validate()
            if candidate.candidate_id in ids:
                raise ValueError("candidate_id values must be unique")
            ids.add(candidate.candidate_id)

    def route_metadata(self) -> dict[str, object]:
        """Render the exact metadata shape consumed by the existing shadow bridge."""
        self.validate()
        return {
            "trace_id": self.trace_id,
            "cache_affinity_shadow": {
                "request": self.request.public_payload(),
                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "eligible": candidate.eligible,
                        "cache_identity": candidate.cache_identity.public_payload(),
                        "observed_ttft_ms": candidate.ttft_ms,
                        "observed_prefill_ms": candidate.prefill_ms,
                        "observed_cache_hit": candidate.cache_hit,
                    }
                    for candidate in self.candidates
                ],
            },
        }

    @property
    def fingerprint(self) -> str:
        payload = self.route_metadata()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def envelope_from_mapping(payload: Mapping[str, Any]) -> CacheAffinityTelemetryEnvelope:
    request = payload.get("request")
    candidates = payload.get("candidates")
    if not isinstance(request, Mapping) or not isinstance(candidates, list):
        raise ValueError("request object and candidates list are required")

    def identity(row: Mapping[str, Any]) -> RuntimeCacheIdentityTelemetry:
        return RuntimeCacheIdentityTelemetry(
            model_hash=str(row["model_hash"]),
            quant=str(row["quant"]),
            context_size=int(row["context_size"]),
            runtime_id=str(row["runtime_id"]),
            session_id=str(row["session_id"]),
            stable_prefix=str(row["stable_prefix"]),
        )

    envelope = CacheAffinityTelemetryEnvelope(
        request=identity(request),
        candidates=tuple(
            CandidateCacheTelemetry(
                candidate_id=str(row["candidate_id"]),
                eligible=bool(row["eligible"]),
                cache_identity=identity(row["cache_identity"]),
                ttft_ms=float(row["ttft_ms"]) if row.get("ttft_ms") is not None else None,
                prefill_ms=float(row["prefill_ms"]) if row.get("prefill_ms") is not None else None,
                cache_hit=bool(row["cache_hit"]) if row.get("cache_hit") is not None else None,
            )
            for row in candidates
            if isinstance(row, Mapping)
        ),
        trace_id=str(payload.get("trace_id") or "").strip() or None,
        correlation_id=str(payload.get("correlation_id") or "").strip() or None,
    )
    envelope.validate()
    return envelope
