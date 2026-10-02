"""Contract tests for the shared signal merge.

``context.merge_signals`` replaced two byte-identical private copies in
``config`` and ``signal_registry``. Signal identity and merge order feed
``PolicyEngine._context_signals`` and therefore model scoring, so these tests pin
the semantics rather than just the shape.
"""

from __future__ import annotations

import pytest

from auto_router import config, signal_registry
from auto_router.context import ContextSignal, merge_signals


def _pre_consolidation_reference(*groups: list[ContextSignal]) -> list[ContextSignal]:
    """The algorithm as it appeared in both originals, verbatim."""

    merged: dict[str, ContextSignal] = {}
    for group in groups:
        for signal in group:
            existing = merged.get(signal.signal_id)
            if existing is None or signal.observed_at >= existing.observed_at:
                merged[signal.signal_id] = signal
    return sorted(
        merged.values(),
        key=lambda item: (item.priority, item.source, item.target_type, item.target_id, item.signal_type),
    )


def _signal(
    signal_id: str,
    *,
    observed_at: int = 100,
    priority: int = 100,
    source: str = "assistx",
    target_type: str = "provider",
    target_id: str = "p",
    signal_type: str = "preferred",
    strength: float = 1.0,
) -> ContextSignal:
    return ContextSignal(
        signal_id=signal_id,
        target_type=target_type,
        target_id=target_id,
        signal_type=signal_type,
        source=source,
        strength=strength,
        observed_at=observed_at,
        priority=priority,
    )


def test_both_modules_delegate_to_the_shared_implementation() -> None:
    assert config._merge_signals is merge_signals
    assert signal_registry._merge_signals is merge_signals


def test_empty_input_returns_empty_output() -> None:
    assert merge_signals() == []
    assert merge_signals([], []) == []


def test_disjoint_groups_are_all_retained() -> None:
    a, b = _signal("a"), _signal("b")
    assert [s.signal_id for s in merge_signals([a], [b])] == ["a", "b"]


def test_same_signal_id_keeps_the_newer_observation() -> None:
    older = _signal("x", observed_at=100, strength=0.1)
    newer = _signal("x", observed_at=200, strength=0.9)
    merged = merge_signals([older, newer])
    assert len(merged) == 1
    assert merged[0].observed_at == 200
    assert merged[0].strength == 0.9


def test_equal_observed_at_lets_the_later_group_win() -> None:
    first = _signal("x", observed_at=100, strength=0.1, source="a")
    second = _signal("x", observed_at=100, strength=0.2, source="b")
    merged = merge_signals([first], [second])
    assert len(merged) == 1
    assert merged[0].source == "b", "ties are broken by group order (>= comparison)"


def test_last_group_wins_on_ties_even_when_reversed_within_a_group() -> None:
    first = _signal("x", observed_at=100, source="a")
    second = _signal("x", observed_at=100, source="b")
    assert merge_signals([first, second])[0].source == "b"
    assert merge_signals([second, first])[0].source == "a"


def test_older_group_cannot_overwrite_a_newer_observation() -> None:
    newer = _signal("x", observed_at=300, source="fresh")
    older = _signal("x", observed_at=200, source="stale")
    assert merge_signals([newer, older])[0].source == "fresh"
    assert merge_signals([older, newer])[0].source == "fresh"


def test_output_ordering_is_priority_then_source_then_target_then_type() -> None:
    low = _signal("low", priority=200)
    high = _signal("high", priority=10)
    assert [s.signal_id for s in merge_signals([low, high])] == ["high", "low"]

    a = _signal("a", source="alpha")
    b = _signal("b", source="beta")
    assert [s.signal_id for s in merge_signals([b, a])] == ["a", "b"]

    provider = _signal("p", target_type="provider", target_id="x")
    model = _signal("m", target_type="model", target_id="x")
    # target_type is the third sort key, so it orders alphabetically: model < provider.
    assert [s.target_type for s in merge_signals([provider, model])] == ["model", "provider"]

    signal_type_a = _signal("s1", target_type="model", target_id="x", signal_type="avoid")
    signal_type_b = _signal("s2", target_type="model", target_id="x", signal_type="blocked")
    assert [s.signal_type for s in merge_signals([signal_type_b, signal_type_a])] == [
        "avoid",
        "blocked",
    ]


def test_merge_is_idempotent() -> None:
    signals = [_signal("a"), _signal("b", priority=5)]
    once = merge_signals(signals)
    twice = merge_signals(once)
    assert [s.signal_id for s in once] == [s.signal_id for s in twice]


@pytest.mark.parametrize(
    "groups",
    [
        ([], []),
        ([_signal("x", observed_at=1)], [_signal("x", observed_at=2)]),
        ([_signal("x", observed_at=2)], [_signal("x", observed_at=1)]),
        ([_signal("a"), _signal("b", priority=7)], [_signal("c", source="z")]),
        (
            [_signal("d", target_type="model", target_id="m", priority=3)],
            [_signal("e", signal_type="blocked", target_type="node", target_id="n")],
        ),
    ],
)
def test_shared_helper_matches_the_pre_consolidation_algorithm(
    groups: tuple[list[ContextSignal], ...],
) -> None:
    reference = _pre_consolidation_reference(*groups)
    shared = merge_signals(*groups)
    assert [s.signal_id for s in shared] == [s.signal_id for s in reference]
    assert [s.observed_at for s in shared] == [s.observed_at for s in reference]
    assert [s.source for s in shared] == [s.source for s in reference]


def test_blocking_signal_survives_the_merge() -> None:
    """A blocking signal must still be blocking after merging, or policy stops
    seeing the block. Guards the property PolicyEngine depends on."""

    blocked = _signal("block", signal_type="blocked", target_type="model", target_id="m")
    preferred = _signal("pref", signal_type="preferred", target_type="model", target_id="m")
    merged = merge_signals([blocked], [preferred])
    by_id = {s.signal_id: s for s in merged}
    assert by_id["block"].is_blocking is True
    assert by_id["pref"].is_blocking is False
