from auto_router.cache_affinity_route_shadow import observe_route_cache_affinity


def _identity(*, runtime_id: str, prefix: str = "system+tools"):
    return {
        "model_hash": "sha-model-a",
        "quant": "Q4_K_M",
        "context_size": 32768,
        "runtime_id": runtime_id,
        "session_id": "session-1",
        "stable_prefix": prefix,
    }


def test_complete_cache_telemetry_produces_non_authoritative_shadow_observation():
    metadata = {
        "cache_affinity_shadow": {
            "request": _identity(runtime_id="runtime-b"),
            "candidates": [
                {
                    "candidate_id": "runtime-a",
                    "eligible": True,
                    "cache_identity": _identity(runtime_id="runtime-a"),
                },
                {
                    "candidate_id": "runtime-b",
                    "eligible": True,
                    "cache_identity": _identity(runtime_id="runtime-b"),
                },
            ],
        }
    }
    result = observe_route_cache_affinity(metadata, actual_candidate_id="runtime-a")
    assert result is not None
    assert result.actual_candidate_id == "runtime-a"
    assert result.affinity_candidate_id == "runtime-b"
    assert result.agreed is False
    assert result.affinity_score == 7


def test_ineligible_perfect_match_cannot_win():
    metadata = {
        "cache_affinity_shadow": {
            "request": _identity(runtime_id="runtime-b"),
            "candidates": [
                {
                    "candidate_id": "runtime-b",
                    "eligible": False,
                    "cache_identity": _identity(runtime_id="runtime-b"),
                },
                {
                    "candidate_id": "runtime-a",
                    "eligible": True,
                    "cache_identity": _identity(runtime_id="runtime-a"),
                },
            ],
        }
    }
    result = observe_route_cache_affinity(metadata, actual_candidate_id="runtime-a")
    assert result is not None
    assert result.affinity_candidate_id == "runtime-a"
    assert result.affinity_score == 0
    assert result.excluded_ineligible == 1


def test_missing_or_malformed_telemetry_is_silently_non_observable():
    assert observe_route_cache_affinity({}, actual_candidate_id="runtime-a") is None
    assert (
        observe_route_cache_affinity(
            {"cache_affinity_shadow": {"request": {}, "candidates": []}},
            actual_candidate_id="runtime-a",
        )
        is None
    )
    assert (
        observe_route_cache_affinity(
            {
                "cache_affinity_shadow": {
                    "request": _identity(runtime_id="runtime-a"),
                    "candidates": [
                        {
                            "candidate_id": "runtime-a",
                            "eligible": "yes",
                            "cache_identity": _identity(runtime_id="runtime-a"),
                        }
                    ],
                }
            },
            actual_candidate_id="runtime-a",
        )
        is None
    )
