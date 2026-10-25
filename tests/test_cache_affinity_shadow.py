from auto_router.cache_affinity import CacheIdentity
from auto_router.cache_affinity_policy import AffinityCandidate
from auto_router.cache_affinity_shadow import observe_cache_affinity


def _identity(*, session="s1", prefix="p1", model="m1", runtime="r1"):
    return CacheIdentity.from_request(
        model_hash=model,
        quant="Q4_K_M",
        context_size=32768,
        runtime_id=runtime,
        session_id=session,
        stable_prefix=prefix,
    )


def test_shadow_reports_disagreement_without_changing_actual():
    request = _identity()
    result = observe_cache_affinity(
        request,
        [
            AffinityCandidate("actual", _identity(session="s2", prefix="p2"), True),
            AffinityCandidate("cache-best", _identity(), True),
        ],
        actual_candidate_id="actual",
    )
    assert result.actual_candidate_id == "actual"
    assert result.affinity_candidate_id == "cache-best"
    assert result.agreed is False
    assert result.affinity_score == 7


def test_shadow_never_selects_ineligible_perfect_match():
    request = _identity()
    result = observe_cache_affinity(
        request,
        [
            AffinityCandidate("blocked", _identity(), False),
            AffinityCandidate("actual", _identity(session="s2", prefix="p2"), True),
        ],
        actual_candidate_id="actual",
    )
    assert result.affinity_candidate_id == "actual"
    assert result.agreed is True
    assert result.excluded_ineligible == 1
