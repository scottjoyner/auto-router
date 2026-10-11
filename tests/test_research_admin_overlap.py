"""Synthetic, value-free proof for an UNWIRED admin-key migration helper."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "auto_router" / "research_admin_overlap.py"
spec = importlib.util.spec_from_file_location("research_admin_overlap", SOURCE)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

PRIMARY = "P" * 48
PREVIOUS = "Q" * 48
WRONG = "R" * 48


def check(presented=PREVIOUS, **changes):
    args = dict(
        primary_token=PRIMARY,
        previous_token=PREVIOUS,
        feature_explicitly_enabled=True,
        owner_window_approved=True,
        starts_at_utc=1_800_000_000,
        expires_at_utc=1_800_000_600,
        activated_at_utc=1_800_000_100,
        observed_at_utc=1_800_000_130,
        activated_monotonic_seconds=1000,
        observed_monotonic_seconds=1030,
    )
    args.update(changes)
    return policy.evaluate_experimental_overlap(presented, **args)


def test_never_enabled_by_default_even_when_old_token_present():
    assert policy.evaluate_experimental_overlap(
        PREVIOUS, primary_token=PRIMARY, previous_token=PREVIOUS
    ) == policy.AuthenticationDecision.DENY


def test_valid_bounded_explicitly_approved_old_credential():
    assert check() == policy.AuthenticationDecision.PREVIOUS_ACCEPTED


def test_primary_still_works_when_overlap_disabled_or_malformed():
    for overrides in (
        dict(feature_explicitly_enabled=False),
        dict(expires_at_utc="broken"),
        dict(owner_window_approved=False),
        dict(previous_token=None),
    ):
        assert check(PRIMARY, **overrides) == policy.AuthenticationDecision.PRIMARY_ACCEPTED


@pytest.mark.parametrize("bad", [False, None, "true", 1, "", [], {}])
def test_enabled_requires_boolean_true(bad):
    assert check(feature_explicitly_enabled=bad) == policy.AuthenticationDecision.DENY


@pytest.mark.parametrize("bad", [False, None, "yes", 1, "", [], {}])
def test_owner_approval_requires_boolean_true(bad):
    assert check(owner_window_approved=bad) == policy.AuthenticationDecision.DENY


@pytest.mark.parametrize("presented", [WRONG, "", None, 123, object(), " Q"*25])
def test_wrong_or_nonstring_credential_denied(presented):
    assert check(presented) == policy.AuthenticationDecision.DENY


@pytest.mark.parametrize("candidate", ["short", "", None, " Z"*28, "X"*48+" ", "🦊"*48, "X\n"*24])
def test_previous_key_must_be_strong_ascii_no_whitespace(candidate):
    assert check(previous_token=candidate) == policy.AuthenticationDecision.DENY


def test_duplicate_old_and_new_keys_never_prove_overlap():
    assert check(primary_token=PREVIOUS) == policy.AuthenticationDecision.PRIMARY_ACCEPTED
    assert check(WRONG, primary_token=PREVIOUS) == policy.AuthenticationDecision.DENY


@pytest.mark.parametrize("changes", [
    dict(starts_at_utc=1_800_000_000, expires_at_utc=1_800_001_000),
    dict(starts_at_utc=1_800_000_600, expires_at_utc=1_800_000_600),
    dict(starts_at_utc=1_800_000_700),
    dict(expires_at_utc=1_800_000_100),
    dict(observed_at_utc=1_800_000_600),
    dict(observed_at_utc=1_800_000_099),
    dict(activated_at_utc=1_799_999_999),
    dict(activated_at_utc=1_800_000_600),
    dict(observed_monotonic_seconds=999),
    dict(observed_monotonic_seconds=1500),
    dict(observed_monotonic_seconds=1501),
    dict(observed_at_utc=1_800_000_100, observed_monotonic_seconds=1100),
    dict(expires_at_utc="600"),
    dict(starts_at_utc=True),
    dict(activated_at_utc=1_800_000_100.1),
    dict(observed_monotonic_seconds=1000.0),
])
def test_invalid_timing_is_fail_closed(changes):
    assert check(**changes) == policy.AuthenticationDecision.DENY


def test_last_allowed_second_then_expiry():
    assert check(
        observed_at_utc=1_800_000_599,
        observed_monotonic_seconds=1499,
    ) == policy.AuthenticationDecision.PREVIOUS_ACCEPTED
    assert check(
        observed_at_utc=1_800_000_600,
        observed_monotonic_seconds=1499,
    ) == policy.AuthenticationDecision.DENY


def test_monotonic_expiry_remains_a_separate_guard():
    assert check(
        observed_at_utc=1_800_000_599,
        observed_monotonic_seconds=1499,
    ) == policy.AuthenticationDecision.PREVIOUS_ACCEPTED
    assert check(
        observed_at_utc=1_800_000_599,
        observed_monotonic_seconds=1500,
    ) == policy.AuthenticationDecision.DENY


def test_backwards_wall_clock_is_denied_even_if_not_utc_expired():
    assert check(
        observed_at_utc=1_800_000_105, observed_monotonic_seconds=1040
    ) == policy.AuthenticationDecision.DENY


def test_never_echoes_credentials(capsys):
    assert check() == policy.AuthenticationDecision.PREVIOUS_ACCEPTED
    output=capsys.readouterr()
    assert PRIMARY not in output.out+output.err
    assert PREVIOUS not in output.out+output.err
    assert repr(policy.AuthenticationDecision.PREVIOUS_ACCEPTED).find(PREVIOUS) == -1


def test_research_helper_is_not_imported_from_production_auth():
    auth_source=(SOURCE.parent / "security.py").read_text()
    live_source=(SOURCE.parent / "secure_live.py").read_text()
    settings_source=(SOURCE.parent / "settings.py").read_text()
    assert "research_admin_overlap" not in auth_source+live_source+settings_source
    assert "admin_token: str" in settings_source


def test_no_environment_network_or_service_actions():
    content=SOURCE.read_text()
    for forbidden in ("os.environ", "getenv(", "subprocess", "socket", "urllib", "logging.", "print(", "docker ", "httpx"):
        assert forbidden not in content
