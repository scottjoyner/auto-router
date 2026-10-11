"""EXPERIMENTAL pure logic for a strictly bounded admin-token overlap.

NOT IMPORTED by the ASGI application, Settings, Compose, or production auth.
Does not read environment, store/log tokens, call networks, or mutate state.
No activation or release authority can be inferred from this module.
"""
from __future__ import annotations

from enum import Enum
import base64
import binascii
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


def evaluate_experimental_header_set(
    raw_headers: object,
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
    """UNWIRED model: reject multiple or conflicting auth presentation channels.

    Accepts synthetic HTTP header (name, value) pairs, not a live Request.
    No parsing ambiguity may grant privileges. Never returns token material.
    A production integrator would additionally need HTTP/TLS and proxy
    boundary validation; this helper is NOT an ASGI dependency.
    """
    if not isinstance(raw_headers, (list, tuple)) or len(raw_headers) > 64:
        return AuthenticationDecision.DENY
    found: list[str] = []
    for pair in raw_headers:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            return AuthenticationDecision.DENY
        name, value = pair
        if (
            not isinstance(name, str) or not name.isascii()
            or not isinstance(value, str) or not value.isascii()
            or not 1 <= len(name) <= 128 or len(value) > 2048
            or not all(33 <= ord(x) <= 126 for x in name)
            or not all(32 <= ord(x) <= 126 for x in value)
        ):
            return AuthenticationDecision.DENY
        normalized = name.lower()
        if normalized == "x-admin-token":
            found.append(value)
        elif normalized == "authorization":
            scheme, sep, encoded = value.partition(" ")
            if sep != " " or not encoded or encoded.startswith(" "):
                return AuthenticationDecision.DENY
            if scheme.lower() == "bearer":
                # No internal whitespace, control chars or comma-folded values.
                if any(x in encoded for x in (" ", ",", "\t")):
                    return AuthenticationDecision.DENY
                found.append(encoded)
            elif scheme.lower() == "basic":
                # Strict, canonical base64 prevents ambiguous header decoding.
                try:
                    packed = encoded.encode("ascii")
                    decoded = base64.b64decode(packed, validate=True)
                    if base64.b64encode(decoded) != packed or len(decoded) > 512:
                        return AuthenticationDecision.DENY
                    decoded_text = decoded.decode("ascii")
                except (ValueError, UnicodeError, binascii.Error):
                    return AuthenticationDecision.DENY
                username, colon, password = decoded_text.partition(":")
                if not colon or username != "admin":
                    return AuthenticationDecision.DENY
                found.append(password)
            else:
                return AuthenticationDecision.DENY
        if len(found) > 1:
            return AuthenticationDecision.DENY
    if len(found) != 1:
        return AuthenticationDecision.DENY
    return evaluate_experimental_overlap(
        found[0], primary_token=primary_token, previous_token=previous_token,
        feature_explicitly_enabled=feature_explicitly_enabled,
        owner_window_approved=owner_window_approved,
        starts_at_utc=starts_at_utc, expires_at_utc=expires_at_utc,
        activated_at_utc=activated_at_utc, observed_at_utc=observed_at_utc,
        activated_monotonic_seconds=activated_monotonic_seconds,
        observed_monotonic_seconds=observed_monotonic_seconds,
    )
