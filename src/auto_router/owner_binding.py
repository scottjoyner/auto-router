"""Deny-only opt-in owner fence for future authenticated multi-user inference.

This does NOT supply a principal. A separately verified gateway middleware
must set scope.state["verified_owner"] *internally* after authentication.
Never read owner fields from request JSON or a client-supplied HTTP header.
AssistX must independently store/issue its immutable authoritative owner
and return it from the claim-status resource before this can be enabled.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


class OwnerBindingError(ValueError):
    pass


def owner_binding_required() -> bool:
    raw = os.getenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "false").strip().lower()
    if raw in {"", "0", "false", "off", "no"}:
        return False
    # Unknown values (including typos) must fail closed, not disable the fence.
    return True


def _identity(value: Any, *, origin: str) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise OwnerBindingError(f"{origin} canonical owner identity absent")
    if "owner_issuer" not in value or "owner_subject" not in value:
        raise OwnerBindingError(f"{origin} canonical owner identity absent")
    issuer = value.get("owner_issuer")
    subject = value.get("owner_subject")
    if any(
        type(v) is not str or not 1 <= len(v) <= 160
        or v.strip() != v or any(ch.isspace() for ch in v)
        for v in (issuer, subject)
    ):
        raise OwnerBindingError(f"{origin} canonical owner identity invalid")
    return issuer, subject


def bound_principal(scope: Mapping[str, Any], claims: Mapping[str, Any]) -> dict[str, str]:
    """Fail if signed claim and separately authenticated ASGI principal differ."""
    if not owner_binding_required():
        return {}
    state = scope.get("state")
    principal = state.get("verified_owner") if isinstance(state, Mapping) else None
    if not isinstance(principal, Mapping) or principal.get("authenticated") is not True:
        raise OwnerBindingError("verified gateway owner principal unavailable")
    auth_owner = _identity(principal, origin="gateway")
    token_owner = _identity(claims, origin="signed token")
    if auth_owner != token_owner:
        raise OwnerBindingError("signed token owner differs from authenticated gateway principal")
    return {"owner_issuer": token_owner[0], "owner_subject": token_owner[1]}


def assert_claim_owner(owner: Mapping[str, Any], active: Mapping[str, Any]) -> None:
    """Compare independently fetched AssistX owner authority before dispatch."""
    if not owner_binding_required():
        return
    issued = _identity(owner, origin="executor")
    live = _identity(active, origin="AssistX claim status")
    if issued != live:
        raise OwnerBindingError("AssistX claim owner no longer matches executor token")
