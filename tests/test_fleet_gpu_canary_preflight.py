"""Deterministic, offline tests for GPU presence and fail-closed preflight."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_display_class_amdgpu_is_detected():
    mod = load("gpu_reporter", "fleet_node_reporter_gpu.py")
    lspci = "c3:00.0 Display controller: Advanced Micro Devices, Inc. [AMD/ATI] Device 1586\n"
    with patch.object(mod.shutil, "which", side_effect=lambda x: x if x == "lspci" else None):
        with patch.object(mod.subprocess, "check_output", return_value=lspci):
            assert mod._linux_gpu() == "AMD"


def test_preflight_does_not_grant_auto_dispatch_on_health_alone():
    mod = load("gpu_preflight", "fleet_gpu_preflight.py")
    report = {"received_at": 1792180000, "health": {"status": "healthy"}, "specs": {"cpu_cores": 24}}
    with patch.object(mod, "fetch_status", side_effect=lambda url, **kw: (200 if url.endswith("/health") else 401, None)):
        with patch.object(mod, "fetch_report", return_value=report):
            with patch.object(mod.time, "time", return_value=1792180001):
                items = mod.preflight(timeout_s=0.1)["nodes"]
    assert all(row["api_reachable_and_guarded"] for row in items)
    assert all(not row["approved_for_auto_dispatch"] for row in items)
    assert all(row["warnings"] == [] for row in items)


def test_preflight_detects_overload_and_auth_drift():
    mod = load("gpu_preflight", "fleet_gpu_preflight.py")
    report = {"received_at": 1792180000, "health": {"status": "unhealthy", "load_1m": 101, "swap_used_gib": 32}, "specs": {"cpu_cores": 24}}
    with patch.object(mod, "fetch_status", return_value=(200, None)):
        with patch.object(mod, "fetch_report", return_value=report):
            with patch.object(mod.time, "time", return_value=1792180001):
                row = mod.preflight(timeout_s=0.1)["nodes"][0]
    assert "authentication_boundary_failed_or_unknown" in row["warnings"]
    assert "excessive_cpu_load" in row["warnings"]
    assert "swap_pressure" in row["warnings"]
    assert not row["approved_for_auto_dispatch"]


def test_gpu_candidate_is_disabled_and_auth_is_required():
    config = yaml.safe_load((ROOT / "config" / "providers.gpu-canary.CANDIDATE.yaml").read_text())
    providers = config["providers"]
    assert len(providers) == 4
    assert all(row["enabled"] is False for row in providers)
    assert providers[0]["models"] == providers[1]["models"] == []
    assert providers[2]["models"][0]["provider_model"] == "toolcall-v5-3b-combined-r2-f16"
    assert providers[3]["models"][0]["provider_model"] == "ternary-bonsai-2-27b-gpu"
    assert all(row["type"] == "openai_compatible" for row in providers)
    assert all(row["api_key_env"] for row in providers)
    assert all(row["allow_code_execution"] is False for row in providers)


def test_rocm_only_healthy_is_degraded_not_healthy():
    mod = load("gpu_reporter_health", "fleet_node_reporter_gpu.py")
    gpu = {
        "gpu_canary_health_http": 200,
        "gpu_canary_anonymous_models_http": 401,
        "gpu_canary_reachable_and_guarded": True,
        "gpu_dispatch_admitted": False,
    }
    with patch.object(mod, "_gpu_canary_status", return_value=gpu):
        with patch.object(mod, "_read_proc_meminfo", return_value={"MemAvailable": 25.0, "SwapTotal": 32.0, "SwapFree": 20.0}):
            health = mod._health([], [])
    assert health["status"] == "degraded"
    assert health["lmstudio_api"] == "down"
    assert health["gpu_canary_reachable_and_guarded"]
    assert health["gpu_dispatch_admitted"] is False


def test_failed_gpu_and_lmstudio_is_unhealthy():
    mod = load("gpu_reporter_failclosed", "fleet_node_reporter_gpu.py")
    gpu = {
        "gpu_canary_health_http": 0,
        "gpu_canary_anonymous_models_http": 0,
        "gpu_canary_reachable_and_guarded": False,
        "gpu_dispatch_admitted": False,
    }
    with patch.object(mod, "_gpu_canary_status", return_value=gpu):
        with patch.object(mod, "_read_proc_meminfo", return_value={}):
            health = mod._health([], [])
    assert health["status"] == "unhealthy"
    assert health["gpu_dispatch_admitted"] is False


def test_bonsai_gpu_candidate_is_not_autoadmitted():
    cfg = yaml.safe_load((ROOT / "config" / "providers.gpu-canary.CANDIDATE.yaml").read_text())
    bonsai = next(x for x in cfg["providers"] if x["name"] == "llama-xwing-bonsai2-vulkan-canary")
    assert bonsai["enabled"] is False
    assert bonsai["models"][0]["quantization"] == "PTQ1_0"
    assert bonsai["parallel_slots"] == 1
    assert not bonsai["allow_agent_runtime"]
    assert not bonsai["allow_code_execution"]


def test_authenticated_probe_restricts_secret_to_private_tailnet_endpoints():
    mod = load("gpu_authenticated", "fleet_gpu_authenticated_probe.py")
    import pytest
    mod.validate_private_url("http://100.108.99.47:18271/v1")
    mod.validate_private_url("http://100.83.215.83:18321")
    for url in ("https://example.com", "http://8.8.8.8", "http://example.com", "file:///etc/passwd", "http://100.64.1.2:18321", "http://100.83.215.83:9999"):
        with pytest.raises(RuntimeError):
            mod.validate_private_url(url)
    with pytest.raises(RuntimeError, match="refusing_redirect"):
        mod.DenyRedirects().redirect_request(None, None, 302, "moved", {}, "http://8.8.8.8")


def test_router_network_check_proves_no_auth_secret_or_admission():
    mod = load("gpu_router_network_probe", "fleet_gpu_router_network_probe.py")
    with patch.object(mod, "get_http_code", side_effect=lambda url, timeout=3.0: 200 if url.endswith("/health") else 401):
        result = mod.probe(timeout=0.1)
    assert result["ready_listeners"] == 4
    assert result["automatic_dispatch"] == "disabled"
    assert all(not row["approved_for_auto_dispatch"] for row in result["endpoints"])


def test_router_network_check_rejects_accidental_auth_bypass():
    mod = load("gpu_router_network_probe_bad", "fleet_gpu_router_network_probe.py")
    with patch.object(mod, "get_http_code", return_value=200):
        result = mod.probe(timeout=0.1)
    assert result["ready_listeners"] == 0
    assert all(not row["network_ready_and_auth_guarded"] for row in result["endpoints"])
