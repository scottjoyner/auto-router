from auto_router.cache_affinity import CacheIdentity
from auto_router.cache_affinity_policy import AffinityCandidate, rank_eligible_affinity


def _identity(*, session="s1", prefix="p1", model="m1", runtime="r1"):
    return CacheIdentity.from_request(
        model_hash=model,
        quant="Q4_K_M",
        context_size=32768,
        runtime_id=runtime,
        session_id=session,
        stable_prefix=prefix,
    )


def test_ineligible_exact_match_cannot_win():
    request = _identity()
    decision = rank_eligible_affinity(
        request,
        [
            AffinityCandidate("ineligible-perfect", _identity(), False),
            AffinityCandidate("eligible-weaker", _identity(session="s2", prefix="p2"), True),
        ],
    )
    assert decision.candidate_id == "eligible-weaker"
    assert decision.excluded_ineligible == 1
    assert decision.score == 1


def test_best_affinity_wins_among_eligible_candidates():
    request = _identity()
    decision = rank_eligible_affinity(
        request,
        [
            AffinityCandidate("weak", _identity(session="s2", prefix="p2"), True),
            AffinityCandidate("prefix", _identity(session="s2", prefix="p1"), True),
            AffinityCandidate("perfect", _identity(), True),
        ],
    )
    assert decision.candidate_id == "perfect"
    assert decision.score == 7


def test_no_eligible_candidates_returns_no_decision():
    decision = rank_eligible_affinity(
        _identity(), [AffinityCandidate("nope", _identity(), False)]
    )
    assert decision.candidate_id is None
    assert decision.considered == 0
    assert decision.excluded_ineligible == 1
