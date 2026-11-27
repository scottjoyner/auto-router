"""Hermetic regression tests for the backlog ``max_tasks`` capacity ceiling (R38).

No network, SQLite, live fleet state or service calls: the policy engine and
quota manager are inert stubs, so these tests only exercise task slicing.
"""
from auto_router.backlog_scheduler import (
    BacklogDryRunRequest,
    BacklogTaskCandidate,
    dry_run_backlog_selection,
)


class _StubPlan:
    stages = []
    profile_name = "backlog_burn"


class _CountingPolicy:
    def __init__(self):
        self.calls = 0

    def plan(self, request):
        self.calls += 1
        return _StubPlan()


class _RaisingPolicy:
    def plan(self, request):
        raise AssertionError("policy engine must not run for an empty selection")


def _tasks(n=3):
    return [
        BacklogTaskCandidate(title=f"task {i}", priority="background", queue_class="background")
        for i in range(n)
    ]


def _run(max_tasks, policy, n=3):
    request = BacklogDryRunRequest(
        tasks=_tasks(n), enqueue_events=False, max_tasks=max_tasks
    )
    return dry_run_backlog_selection(request, policy_engine=policy, quota=None)


def test_zero_limit_selects_no_tasks():
    decisions = _run(0, _CountingPolicy())
    assert len(decisions) == 0


def test_negative_limit_selects_no_tasks():
    decisions = _run(-1, _CountingPolicy())
    assert len(decisions) == 0


def test_positive_limit_is_respected():
    decisions = _run(2, _CountingPolicy())
    assert len(decisions) == 2


def test_none_limit_selects_all_tasks():
    decisions = _run(None, _CountingPolicy())
    assert len(decisions) == 3


def test_zero_limit_never_dispatches_to_policy_engine():
    # A "select none" ceiling must not even reach planning -- proof that the
    # fix avoids a real dispatch, not just cosmetic truncation of decisions.
    decisions = _run(0, _RaisingPolicy())
    assert decisions == []
