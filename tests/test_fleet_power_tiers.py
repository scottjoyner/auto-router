"""Unit tests for fleet power-tier gating (FLEET-STANDARD-LOADOUTS.md).

Covers the pure logic: report stamping, node->provider mapping merge,
stage-aware blocking, and the backlog power brake decision. No app startup.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from auto_router.main import _apply_power_tiers, _power_tier_apply  # noqa: E402
from auto_router.models import StagePurpose  # noqa: E402
from auto_router.policy import PolicyEngine  # noqa: E402


# --------------------------------------------------------------------------- #
# _power_tier_apply
# --------------------------------------------------------------------------- #
def test_conserve_clamps_score_and_keeps_ok():
    report = {"health_score": 88, "ok": True}
    _power_tier_apply(report, "conserve", "essential")
    assert report["ok"] is True
    assert report["health_score"] == 40
    assert report["power_profile"] == "conserve"
    assert report["power_model_class"] == "essential"


def test_survive_excludes():
    report = {"health_score": 90, "ok": True, "model_count": 3}
    _power_tier_apply(report, "survive")
    assert report["ok"] is False
    assert report["model_count"] == 0
    assert report["health_score"] == 0


def test_daily_is_annotate_only():
    report = {"health_score": 77, "ok": True}
    _power_tier_apply(report, "daily", "full")
    assert report["health_score"] == 77
    assert report["ok"] is True
    assert report["power_model_class"] == "full"


# --------------------------------------------------------------------------- #
# _apply_power_tiers over a health map
# --------------------------------------------------------------------------- #
def test_apply_power_tiers_maps_by_node(monkeypatch):
    import auto_router.main as M

    state_info = {
        "scott-lenovo-ideapad-330s-15ikb": {"profile": "conserve", "model_class": "essential"}
    }
    monkeypatch.setattr(M, "_power_state_by_node", lambda: dict(state_info))
    monkeypatch.setattr(
        M, "_providers_by_node",
        lambda: {"scott-lenovo-ideapad-330s-15ikb": ["lmstudio-lenovo-ideapad-330s-15ikb"]},
    )
    health = {"lmstudio-lenovo-ideapad-330s-15ikb": {"health_score": 80, "ok": True}}
    _apply_power_tiers(health)
    entry = health["lmstudio-lenovo-ideapad-330s-15ikb"]
    assert entry["power_profile"] == "conserve"
    assert entry["power_model_class"] == "essential"
    assert entry["health_score"] == 40


# --------------------------------------------------------------------------- #
# stage-aware policy gate (unbound call; only touches provider_health)
# --------------------------------------------------------------------------- #
def _engine_with(entry):
    eng = SimpleNamespace(provider_health={"prov-x": entry})
    return PolicyEngine._provider_power_blocked.__get__(eng)


def test_survive_blocks_all_stages():
    blocked = _engine_with({"power_profile": "survive"})
    assert blocked("prov-x", None) is True
    assert blocked("prov-x", StagePurpose.draft) is True


def test_conserve_essential_blocks_quality_stages_only():
    blocked = _engine_with({"power_profile": "conserve", "power_model_class": "essential"})
    assert blocked("prov-x", StagePurpose.final) is True
    assert blocked("prov-x", StagePurpose.refine) is True
    assert blocked("prov-x", StagePurpose.judge) is True
    # draft/quick stages stay alive on the essential model
    assert blocked("prov-x", StagePurpose.draft) is False
    assert blocked("prov-x", None) is False


def test_conserve_full_model_never_blocks():
    blocked = _engine_with({"power_profile": "conserve", "power_model_class": "full"})
    for purpose in (StagePurpose.draft, StagePurpose.refine, StagePurpose.final):
        assert blocked("prov-x", purpose) is False


def test_no_entry_never_blocks():
    blocked = _engine_with({"unrelated": True}) if False else _engine_with({})
    assert blocked("missing-provider", StagePurpose.final) is False


def test_missing_report_shape_is_ignored():
    blocked = _engine_with("not-a-dict")
    assert blocked("prov-x", StagePurpose.final) is False
