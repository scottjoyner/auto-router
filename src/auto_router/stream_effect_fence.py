"""Default-off stream effect fence adapter, NOT a production authority.

An independent authenticated, epoch-fenced authority MUST be injected by
trusted initialization as state.stream_effect_authority. Neither headers,
request JSON nor caller-supplied metadata may supply that object.

Even when a future broker exists, a stream ACK precedes attempted output
delivery; that does not prove socket delivery and DB acceptance are atomic.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any


class StreamEffectDenied(RuntimeError):
    """Stream has uncertain upstream acceptance and must release no further chunks."""


def effect_fence_required() -> bool:
    # Unknown values fail closed as enabled, matching owner-binding design.
    return os.getenv("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE", "false").strip().lower() not in {
        "", "0", "false", "off", "no"
    }


def _strict_owner_enabled() -> bool:
    return os.getenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "false").strip().lower() not in {
        "", "0", "false", "off", "no"
    }


def require_authority(state: Any) -> Any | None:
    if not effect_fence_required():
        return None
    if not _strict_owner_enabled():
        raise StreamEffectDenied("effect fencing requires strict authenticated owner mode")
    authority = getattr(state, "stream_effect_authority", None)
    if not all(callable(getattr(authority, name, None)) for name in (
        "preflight", "accept_chunk", "abort"
    )):
        raise StreamEffectDenied("trusted streaming effect authority unavailable")
    return authority


async def preflight(authority: Any, request: Any, candidate: Any) -> None:
    if authority is None:
        return
    try:
        # The real implementation must validate canonical issuer, claim,
        # owner, model and current epoch with a resource-owned broker.
        permitted = await asyncio.wait_for(
            authority.preflight(request=request, candidate=candidate), timeout=2.0
        )
        if permitted is not True:
            raise StreamEffectDenied("stream preflight not authorized")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise StreamEffectDenied("stream preflight authority unavailable or refused") from exc


async def authorize_chunk(
    authority: Any, request: Any, candidate: Any, chunk: bytes, sequence: int
) -> None:
    if authority is None:
        return
    if type(chunk) is not bytes or not chunk or len(chunk) > 8 * 1024 * 1024:
        raise StreamEffectDenied("invalid or oversized fenced stream chunk")
    if type(sequence) is not int or not 1 <= sequence <= 100_000:
        raise StreamEffectDenied("stream chunk sequence invalid")
    try:
        # The future trusted authority must perform an atomic fence check and
        # effect commitment. Never treat a provider-success flag as authority.
        result = await asyncio.wait_for(
            authority.accept_chunk(
                request=request,
                candidate=candidate,
                sequence=sequence,
                chunk_sha256=hashlib.sha256(chunk).hexdigest(),
            ),
            timeout=2.0,
        )
        if result is not True:
            raise StreamEffectDenied("chunk not authorized by current execution epoch")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise StreamEffectDenied("chunk effect authority unavailable or denied") from exc


async def abort_upstream(authority: Any, request: Any, upstream: Any) -> bool:
    """Best-effort bounded abort; return true only if both closes succeeded.

    Failure to abort the model is a separately tracked NO-GO, never proof
    that inference stopped. Cancellation of the client iteration must still
    revoke its eligibility to emit additional bytes.
    """
    closed = False
    if callable(getattr(upstream, "aclose", None)):
        try:
            await asyncio.wait_for(upstream.aclose(), timeout=2.0)
            closed = True
        except BaseException:
            closed = False
    if authority is None:
        return closed
    try:
        result = await asyncio.wait_for(authority.abort(request=request), timeout=2.0)
        return closed and result is True
    except BaseException:
        return False
