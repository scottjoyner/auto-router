from auto_router.cache_affinity import CacheIdentity, affinity_score


def _identity(**overrides):
    values = {
        "model_hash": "model-a",
        "quant": "Q4_K_M",
        "context_size": 32768,
        "runtime_id": "runtime-a",
        "session_id": "session-a",
        "stable_prefix": "system prompt + tools",
    }
    values.update(overrides)
    return CacheIdentity.from_request(**values)


def test_runtime_restart_invalidates_affinity():
    assert affinity_score(_identity(), _identity(runtime_id="runtime-b")) == 0


def test_model_reload_with_new_hash_invalidates_affinity():
    assert affinity_score(_identity(), _identity(model_hash="model-b")) == 0


def test_quant_change_invalidates_affinity():
    assert affinity_score(_identity(), _identity(quant="Q8_0")) == 0


def test_context_change_invalidates_affinity():
    assert affinity_score(_identity(), _identity(context_size=65536)) == 0


def test_prefix_change_reduces_but_does_not_fake_exact_hit():
    score = affinity_score(_identity(), _identity(stable_prefix="changed prompt"))
    assert score == 3  # base compatibility + same-session only
