"""Fail-closed P0 #37 read-only host pressure checks; no SSH in tests."""
from __future__ import annotations

import json
import subprocess

import pytest

from scripts import p0_37_host_pressure_preflight as pressure


def _sample(**changes):
    base = {
        "schema": "p0-37-readonly-host-pressure-v1",
        "host": "BEELINK-RYZEN-7-MINI-PC",
        "observed_at_unix": 1000.0,
        "root_total_bytes": 500 * 1024**3,
        "root_free_user_bytes": 100 * 1024**3,
        "root_free_system_bytes": 110 * 1024**3,
        "io_full_avg10_pct": 0.5,
        "io_some_avg10_pct": 1.5,
        "load1": 0.3,
        "cpu_count": 8,
        "swap_total_kib": 0,
        "swap_free_kib": 0,
        "nas_is_mount": True,
    }
    base.update(changes)
    return base


def _assess(sample, *, now=1001, expected="BEELINK-RYZEN-7-MINI-PC"):
    return pressure.assess(
        sample, expected_host=expected, now=now, require_nas_mount=True
    )


def test_root_near_full_and_io_stall_force_no_go_with_no_real_permission():
    result = _assess(
        _sample(
            root_free_user_bytes=3 * 1024**3,
            io_full_avg10_pct=17.76,
        )
    )
    assert result["host_pressure_status"] == "NO_GO"
    assert result["physical_inference_permitted"] is False
    assert result["provider_execution_attested"] is False
    assert "root_free_below_10_gib" in result["blockers"]
    assert "root_unprivileged_use_at_least_90_pct" in result["blockers"]
    assert "io_full_avg10_at_least_5_pct" in result["blockers"]


def test_good_headroom_still_never_approves_inference_or_authentication():
    result = _assess(_sample())
    assert result["host_pressure_status"] == "HEADROOM_ONLY"
    assert result["blockers"] == []
    assert result["physical_inference_permitted"] is False
    assert result["real_two_user_authentication_proven"] is False
    assert result["operator_approved"] is False


@pytest.mark.parametrize("changes,uncertainty", [
    ({"observed_at_unix": 800}, "sample_missing_stale_or_future"),
    ({"observed_at_unix": 2000}, "sample_missing_stale_or_future"),
    ({"root_total_bytes": 0}, "root_filesystem_counters_invalid"),
    ({"root_free_user_bytes": None}, "root_filesystem_counters_invalid"),
    ({"root_free_user_bytes": True}, "root_filesystem_counters_invalid"),
    ({"io_full_avg10_pct": None}, "io_full_pressure_unavailable"),
    ({"io_full_avg10_pct": -1}, "io_full_pressure_unavailable"),
])
def test_untrusted_missing_or_stale_evidence_fails_closed(changes, uncertainty):
    result = _assess(_sample(**changes))
    assert result["host_pressure_status"] == "UNKNOWN"
    assert uncertainty in result["uncertainties"]
    assert result["physical_inference_permitted"] is False


def test_wrong_host_and_missing_nas_mount_cannot_be_a_positive_gate():
    wrong = _assess(_sample(), expected="x1-370")
    assert wrong["host_pressure_status"] == "UNKNOWN"
    assert "host_identity_not_matched" in wrong["uncertainties"]
    no_nas = _assess(_sample(nas_is_mount=False))
    assert no_nas["host_pressure_status"] == "NO_GO"
    assert "nas_mount_not_confirmed" in no_nas["blockers"]


def test_ssh_collection_uses_only_known_static_readonly_command(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, json.dumps(_sample()), "")

    monkeypatch.setattr(pressure.subprocess, "run", fake_run)
    result = pressure.collect_remote("beelink")
    assert result["host"] == "BEELINK-RYZEN-7-MINI-PC"
    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert "StrictHostKeyChecking=yes" in argv
    assert "BatchMode=yes" in argv
    assert argv[-2] == "beelink"
    assert argv[-1].startswith("python3 -c ")
    assert "ssh" not in argv[-1]
    assert captured["kwargs"]["timeout"] == 12


@pytest.mark.parametrize("host", ["", "-oProxyCommand=bad", "user@host", "abc;touch/tmp/x"])
def test_unsafe_ssh_host_argument_is_rejected(host, monkeypatch):
    def no_execute(*args, **kwargs):
        pytest.fail("unsafe hostname must not execute anything")

    monkeypatch.setattr(pressure.subprocess, "run", no_execute)
    with pytest.raises(ValueError):
        pressure.collect_remote(host)


def test_cli_remote_probe_no_go_has_exit_code_two(monkeypatch, capsys):
    monkeypatch.setattr(
        pressure, "collect_remote",
        lambda host: _sample(
            observed_at_unix=pressure.time.time(),
            root_free_user_bytes=3 * 1024**3,
            io_full_avg10_pct=17.76,
        ),
    )
    code = pressure.main([
        "--ssh-host", "beelink", "--expect-host", "BEELINK-RYZEN-7-MINI-PC",
        "--require-nas-mount", "--json",
    ])
    data = json.loads(capsys.readouterr().out)
    assert code == 2
    assert data["host_pressure_status"] == "NO_GO"
    assert data["physical_inference_permitted"] is False


def test_cli_collect_failure_unknown_no_execution_permitted(monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 12)

    monkeypatch.setattr(pressure, "collect_remote", unavailable)
    code = pressure.main([
        "--ssh-host", "beelink", "--expect-host", "BEELINK-RYZEN-7-MINI-PC", "--json",
    ])
    data = json.loads(capsys.readouterr().out)
    assert code == 3
    assert data["host_pressure_status"] == "UNKNOWN"
    assert data["physical_inference_permitted"] is False
