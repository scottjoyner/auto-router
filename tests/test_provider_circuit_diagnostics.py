"""Model circuit status does not become a fabricated physical outage."""
from auto_router.provider_circuit_diagnostics import (
    annotate_health_with_open_model_circuits as show,
)


def test_embedding_breaker_keeps_provider_denied_without_losing_direct_probe_health():
    source = {"provider": "lmstudio-xwing", "ok": True, "detail": "HTTP 200",
              "metadata": {"raw_model_count": 16}}
    out = show(source, "lmstudio-xwing",
               {"lmstudio-xwing/local/nomic-embed-text-v1.5"})
    assert out["ok"] is False
    assert out["detail"].startswith("Circuit open:")
    assert out["metadata"]["transport_probe_ok_before_model_circuit_overlay"] is True
    assert out["metadata"]["open_model_circuit_count"] == 1
    assert out["metadata"]["model_circuit_overlay_active"] is True
    assert out["metadata"]["raw_model_count"] == 16
    assert source == {"provider": "lmstudio-xwing", "ok": True, "detail": "HTTP 200",
                      "metadata": {"raw_model_count": 16}}


def test_actual_endpoint_failure_is_not_reported_as_reachable():
    out = show({"provider": "lmstudio-xwing", "ok": False,
                "detail": "Connect timeout", "metadata": {}},
               "lmstudio-xwing", {"lmstudio-xwing/local/nomic-embed-text-v1.5"})
    assert out["ok"] is False
    assert out["metadata"]["transport_probe_ok_before_model_circuit_overlay"] is False


def test_no_circuit_preserves_report_boolean_and_detail():
    result = show({"provider": "good", "ok": True, "detail": "HTTP 200",
                   "metadata": {}}, "good", set())
    assert result["ok"] is True
    assert result["detail"] == "HTTP 200"
    assert result["metadata"]["open_model_circuit_count"] == 0


def test_other_provider_circuits_do_not_poison_healthy_provider():
    result = show({"provider": "xwing", "ok": True, "detail": "HTTP 200"},
                  "xwing", {"xwing-bonsai/local/ternary", "xwing2/local/ornith"})
    assert result["ok"] is True
    assert result["metadata"]["transport_probe_ok_before_model_circuit_overlay"] is True


def test_multiple_open_circuits_have_bounded_detail_but_full_count():
    names = {f"xwing/local/ab-{i:04d}" for i in range(35)}
    result = show({"provider": "xwing", "ok": True}, "xwing", names)
    assert result["metadata"]["open_model_circuit_count"] == 35
    assert result["detail"].count("xwing/") == 3
    assert result["ok"] is False


def test_non_dict_metadata_is_fail_safe():
    result = show({"ok": True, "metadata": None}, "xwing", ())
    assert result["ok"] is True
    assert result["metadata"]["model_circuit_overlay_active"] is False
