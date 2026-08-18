from auto_router.cache_affinity_report import evaluate_cache_affinity_shadow
from auto_router.cache_affinity_shadow import CacheAffinityShadowResult


def test_cache_affinity_report_aggregates_shadow_outcomes():
    report = evaluate_cache_affinity_shadow(
        [
            CacheAffinityShadowResult("a", "a", 7, True, 2, 0),
            CacheAffinityShadowResult("b", "c", 5, False, 3, 1),
            CacheAffinityShadowResult("d", None, 0, False, 0, 2),
        ]
    )
    assert report.observations == 3
    assert report.agreement_rate == 1 / 3
    assert report.disagreement_count == 2
    assert report.mean_affinity_score == 4
    assert report.total_excluded_ineligible == 3
    assert report.no_candidate_count == 1


def test_empty_cache_affinity_report_is_stable():
    report = evaluate_cache_affinity_shadow([])
    assert report.observations == 0
    assert report.agreement_rate == 0.0
    assert report.mean_affinity_score == 0.0
