"""EXPERIMENTAL pure logic for a strictly bounded admin-token overlap.

NOT IMPORTED by the ASGI application, Settings, Compose, or production auth.
Does not read environment, store/log tokens, call networks, or mutate state.
No activation or release authority can be inferred from this module.
"""
from __future__ import annotations

from enum import Enum
import secrets

MAX_OVERLAP_SECONDS = 900
MAX_CLOCK_ROLLBACK_SKEW_SECONDS = 2
MIN_TOKEN_CHARS = 32


class AuthenticationDecision(str, Enum):
    DENY = "DENY"
    PRIMARY_ACCEPTED = "PRIMARY_ACCEPTED"
    PREVIOUS_ACCEPTED = "PREVIOUS_ACCEPTED"


def _token_usable(token: object) -> bool:
    return (
        isinstance(token, str)
        and token.isascii()
        and len(token) >= MIN_TOKEN_CHARS
        and token == token.strip()
        and all(32 <= ord(ch) < 127 for ch in token)
    )


def _true_int(value: object) -> bool:
    return type(value) is int


def _valid_overlap_clock(
    *,
    starts_at_utc: object,
    expires_at_utc: object,
    activated_at_utc: object,
    observed_at_utc: object,
    activated_monotonic_seconds: object,
    observed_monotonic_seconds: object,
) -> bool:
    if not all(_true_int(x) for x in (
        starts_at_utc, expires_at_utc, activated_at_utc, observed_at_utc,
        activated_monotonic_seconds, observed_monotonic_seconds,
    )):
        return False
    if not (
        starts_at_utc <= activated_at_utc < expires_at_utc
        and 0 < expires_at_utc - starts_at_utc <= MAX_OVERLAP_SECONDS
        and activated_at_utc <= observed_at_utc < expires_at_utc
        and activated_monotonic_seconds <= observed_monotonic_seconds
    ):
        return False
    elapsed = observed_monotonic_seconds - activated_monotonic_seconds
    if elapsed >= expires_at_utc - activated_at_utc:
        return False
    if observed_at_utc + MAX_CLOCK_ROLLBACK_SKEW_SECONDS < activated_at_utc + elapsed:
        return False
    return True


def evaluate_experimental_overlap(
    presented_token: object,
    *,
    primary_token: object,
    previous_token: object = None,
    feature_explicitly_enabled: object = False,
    owner_window_approved: object = False,
    starts_at_utc: object = None,
    expires_at_utc: object = None,
    activated_at_utc: object = None,
    observed_at_utc: object = None,
    activated_monotonic_seconds: object = None,
    observed_monotonic_seconds: object = None,
) -> AuthenticationDecision:
    """Check *one* credential. Production auth remains entirely untouched.

    Caller must verify the credential transport, reject conflicting/multiple
    Authorization channels, bind to authenticated router peer and supply a
    trustworthy wall clock plus per-process monotonic activation anchor.
    """
    if not _token_usable(presented_token) or not _token_usable(primary_token):
        return AuthenticationDecision.DENY

    # Primary authentication must never depend on the optional overlap state.
    if secrets.compare_digest(presented_token, primary_token):
        return AuthenticationDecision.PRIMARY_ACCEPTED

    if feature_explicitly_enabled is not True or owner_window_approved is not True:
        return AuthenticationDecision.DENY
    if not _token_usable(previous_token):
        return AuthenticationDecision.DENY
    if secrets.compare_digest(primary_token, previous_token):
        return AuthenticationDecision.DENY
    if not _valid_overlap_clock(
        starts_at_utc=starts_at_utc,
        expires_at_utc=expires_at_utc,
        activated_at_utc=activated_at_utc,
        observed_at_utc=observed_at_utc,
        activated_monotonic_seconds=activated_monotonic_seconds,
        observed_monotonic_seconds=observed_monotonic_seconds,
    ):
        return AuthenticationDecision.DENY
    if secrets.compare_digest(presented_token, previous_token):
        return AuthenticationDecision.PREVIOUS_ACCEPTED
    return AuthenticationDecision.DENY
