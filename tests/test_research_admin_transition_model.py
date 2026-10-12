"""Four-service admin-token cutover MODEL ONLY; no real requests or credentials.

Proves there is no always-available single-token sequential cutover ordering
under constant simultaneous demand from API/worker/Hermes.
"""
from __future__ import annotations

from itertools import permutations

import pytest

PARTICIPANTS = ("router", "api", "worker", "hermes")
CONSUMERS = PARTICIPANTS[1:]
OLD = "old-abstract-generation"
NEW = "new-abstract-generation"


def unavailable_consumers(state: dict[str, str], *, old_token_still_accepted: bool = False) -> set[str]:
    accepted = {state["router"]}
    if old_token_still_accepted and state["router"] == NEW:
        accepted.add(OLD)
    return {n for n in CONSUMERS if state[n] not in accepted}


def trace(order, *, overlapping_router: bool = False, expiration_after_step: int | None = None):
    state = {n: OLD for n in PARTICIPANTS}
    timeline = []
    for step, name in enumerate(order, start=1):
        state[name] = NEW
        expired = expiration_after_step is not None and step >= expiration_after_step
        live_overlap = overlapping_router and state["router"] == NEW and not expired
        timeline.append((step, name, unavailable_consumers(state, old_token_still_accepted=live_overlap)))
    return timeline


@pytest.mark.parametrize("order", list(permutations(PARTICIPANTS)))
def test_every_serial_single_token_order_has_an_intermediate_denial(order):
    timeline = trace(order)
    assert any(failed for _step, _name, failed in timeline)
    assert not timeline[-1][2]


def test_router_first_immediately_breaks_all_three_consumers():
    timeline = trace(PARTICIPANTS)
    assert timeline[0][2] == set(CONSUMERS)


def test_client_first_breaks_that_client_until_router_change():
    timeline = trace(("worker", "api", "hermes", "router"))
    assert "worker" in timeline[0][2]
    assert timeline[-1][2] == set()


def test_approved_drained_window_can_avoid_requests_during_mismatched_state():
    order = ("router", "api", "worker", "hermes")
    timeline = trace(order)
    # A maintenance freeze means **no calls during the transition**, not zero downtime.
    traffic_allowed = [False, False, False, True]
    assert all(not (traffic_allowed[i] and failure)
               for i, (_step, _name, failure) in enumerate(timeline))
    assert not any(traffic_allowed[:3])


def test_theoretical_bounded_overlap_only_helps_if_all_clients_finish_before_expiry():
    # Even an overlap-aware router cannot avoid every sequence. Router must
    # enable overlap before any client adopts the new key.
    good = trace(("router", "api", "worker", "hermes"),
                 overlapping_router=True, expiration_after_step=5)
    assert all(not denied for _step, _who, denied in good)
    expired = trace(("router", "api", "worker", "hermes"),
                    overlapping_router=True, expiration_after_step=3)
    assert expired[2][2] == {"hermes"}


def test_overlap_does_not_help_when_client_rolls_first():
    bad=trace(("api", "router", "worker", "hermes"), overlapping_router=True)
    assert bad[0][2] == {"api"}


def test_model_is_about_admin_consumer_traffic_only():
    # This model deliberately contains no signed runtime projection, APNs,
    # inference, graph witness, provider readiness, TLS, source addresses,
    # credentials, or measured SLA.
    assert set(PARTICIPANTS)=={"router","api","worker","hermes"}
    assert all(isinstance(x, str) for x in (OLD,NEW))
