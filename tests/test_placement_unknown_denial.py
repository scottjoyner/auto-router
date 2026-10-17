"""Unknown residency must never authorize a speculative model load."""
from types import SimpleNamespace

from auto_router import model_placement as placement


def test_unknown_residency_cannot_schedule_load(monkeypatch):
    key = "test-model"
    provider = "test-lmstudio"
    snapshot = SimpleNamespace(provider=provider, ok=True, latency_ms=1, models=[
        {"id": key, "residency": "unknown", "endpoint": "http://example.invalid"}
    ])
    state = SimpleNamespace(
        model_registry=SimpleNamespace(latest_inventory=lambda: [snapshot]),
        providers=SimpleNamespace(providers=[SimpleNamespace(
            type="lmstudio", name=provider, base_url="http://example.invalid/v1")]),
    )
    monkeypatch.setattr(placement, "DESIRED_PLACEMENTS", [
        placement.DesiredLoad(provider, key, 4096, "test")])
    plan = placement.compute_plan(state)
    assert not any(a["model_key"] == key for a in plan["loads_needed"])
