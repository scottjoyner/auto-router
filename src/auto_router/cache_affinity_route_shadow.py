"""Bridge real route decisions to cache-affinity shadow evidence when telemetry exists.

The bridge is intentionally fail-closed for observation: incomplete or malformed
cache telemetry yields no shadow result. It never changes the authoritative route.
"""

from __future__ import annotations

from typing import Any, Mapping

from .cache_affinity import CacheIdentity
from .cache_affinity_policy import AffinityCandidate
from .cache_affinity_shadow import CacheAffinityShadowResult, observe_cache_affinity


def _identity(payload: Mapping[str, Any]) -> CacheIdentity:
    return CacheIdentity.from_request(
        model_hash=str(payload["model_hash"]),
        quant=str(payload["quant"]),
        context_size=int(payload["context_size"]),
        runtime_id=str(payload["runtime_id"]),
        session_id=str(payload["session_id"]),
        stable_prefix=str(payload["stable_prefix"]),
    )


def observe_route_cache_affinity(
    metadata: Mapping[str, Any],
    *,
    actual_candidate_id: str | None,
) -> CacheAffinityShadowResult | None:
    """Build a shadow observation from explicit runtime cache telemetry.

    Expected metadata shape::

        cache_affinity_shadow: {
          request: {model_hash, quant, context_size, runtime_id, session_id, stable_prefix},
          candidates: [
            {
              candidate_id: "runtime-x",
              eligible: true,
              cache_identity: {...same identity fields...}
            }
          ]
        }

    This shape is deliberately explicit so route code does not infer cache state
    from provider configuration or fabricate session/prefix residency.
    """
    envelope = metadata.get("cache_affinity_shadow")
    if not isinstance(envelope, Mapping):
        return None
    request_payload = envelope.get("request")
    candidate_payloads = envelope.get("candidates")
    if not isinstance(request_payload, Mapping) or not isinstance(candidate_payloads, list):
        return None

    try:
        request_identity = _identity(request_payload)
        candidates: list[AffinityCandidate] = []
        for row in candidate_payloads:
            if not isinstance(row, Mapping):
                return None
            candidate_id = str(row.get("candidate_id") or "").strip()
            cache_identity = row.get("cache_identity")
            eligible = row.get("eligible")
            if not candidate_id or not isinstance(cache_identity, Mapping) or not isinstance(eligible, bool):
                return None
            candidates.append(
                AffinityCandidate(
                    candidate_id=candidate_id,
                    cache_identity=_identity(cache_identity),
                    eligible=eligible,
                )
            )
    except (KeyError, TypeError, ValueError):
        return None

    if not candidates:
        return None
    return observe_cache_affinity(
        request_identity,
        candidates,
        actual_candidate_id=actual_candidate_id,
    )
