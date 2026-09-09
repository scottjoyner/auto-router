from auto_router.cache_affinity import CacheIdentity, affinity_score


def _identity(**overrides):
    values = {
        "model_hash": "model-abc",
        "quant": "Q4_K_M",
        "context_size": 32768,
        "runtime_id": "runtime-1",
        "session_id": "session-1",
        "stable_prefix": "system prompt + tool schema",
    }
    values.update(overrides)
    return CacheIdentity.from_request(**values)


def test_exact_session_and_prefix_match_scores_highest():
    request = _identity()
    candidate = _identity()
    assert affinity_score(request, candidate) == 7


def test_same_runtime_without_session_or_prefix_still_has_base_affinity():
    request = _identity()
    candidate = _identity(session_id="session-2", stable_prefix="different")
    assert affinity_score(request, candidate) == 1


def test_model_hash_mismatch_forces_zero_affinity():
    assert affinity_score(_identity(), _identity(model_hash="different-model")) == 0


def test_quant_mismatch_forces_zero_affinity():
    assert affinity_score(_identity(), _identity(quant="Q8_0")) == 0


def test_context_mismatch_forces_zero_affinity():
    assert affinity_score(_identity(), _identity(context_size=8192)) == 0


def test_runtime_mismatch_forces_zero_affinity():
    assert affinity_score(_identity(), _identity(runtime_id="runtime-2")) == 0
