"""Read-only Linux process attribution preflight: no production socket required."""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/p0_37_linux_listener_preflight.py"
spec = importlib.util.spec_from_file_location("p037_linux_listener_preflight", MODULE)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def _proc(tmp_path: Path, *, listener=True, fd=True):
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text(str(uuid.uuid4()) + "\n")
    header = (
        "sl local_address rem_address st tx_queue rx_queue "
        "tr tm->when retrnsmt uid timeout inode"
    )
    rows = [header]
    if listener:
        rows.append(
            "0: 0100007F:04D2 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000 1000 0 424242 1 000000"
        )
    (proc / "net/tcp").write_text("\n".join(rows) + "\n")
    (proc / "net/tcp6").write_text(header + "\n")
    worker = proc / "1212"
    worker.mkdir()
    # stat field 22 = index 19 in array AFTER the closing ')'.
    (worker / "stat").write_text(
        "1212 (sample process (name)) S " + " ".join(
            ["1"] * 18 + ["678901"] + ["1"] * 3
        ) + "\n"
    )
    if fd:
        (worker / "fd").mkdir()
        (worker / "fd/3").symlink_to("socket:[424242]")
    return proc


def test_exact_listener_socket_pid_and_starttime_candidate_never_attests(
    tmp_path,
):
    proc = _proc(tmp_path)
    r = preflight.collect(proc_root=proc, port=1234)
    assert r["listener_state"] == "listening"
    assert r["listener_socket_count"] == 1
    assert r["scan_state"] == "complete"
    assert r["inaccessible_fd_directory_count"] == 0
    assert r["matching_processes"] == [{
        "pid": 1212, "process_start_ticks": 678901,
        "boot_id": r["boot_id"],
        "socket_inode_correlated": True,
        "fd_scan_complete": True,
        "native_provider_attempt_witness": False,
    }]
    assert r["provider_execution_attested"] is False
    assert r["physical_inference_permitted"] is False
    assert r["model_artifact_sha256_measured"] is False
    assert r["native_per_attempt_request_observed"] is False


def test_permission_unavailable_is_unknown_not_no_process(tmp_path):
    proc = _proc(tmp_path, fd=False)
    r = preflight.collect(proc_root=proc)
    assert r["listener_state"] == "listening"
    assert r["scan_state"] == "partial_unattributed"
    assert r["matching_processes"] == []
    assert r["inaccessible_fd_directory_count"] == 1
    assert "some_process_fd_directories_inaccessible" in r[
        "observation_unknown_reasons"
    ]


def test_no_listener_is_not_proof_of_any_model_execution(tmp_path):
    proc = _proc(tmp_path, listener=False)
    r = preflight.collect(proc_root=proc)
    assert r["listener_state"] == "not_listening"
    assert r["matching_processes"] == []
    assert r["native_per_attempt_request_observed"] is False
    assert r["provider_execution_attested"] is False


def test_start_ticks_parse_handles_parentheses_in_process_comm(tmp_path):
    proc = _proc(tmp_path)
    assert preflight._start_ticks(proc, 1212) == 678901


def test_process_starttime_change_never_gives_stable_owner(tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    values = iter([10, 11])
    monkeypatch.setattr(preflight, "_start_ticks", lambda _root, _pid: next(values))
    r = preflight.collect(proc_root=proc)
    assert not r["matching_processes"]
    assert "process_changed_during_snapshot" in r["observation_unknown_reasons"]
    assert r["scan_state"] == "partial_unattributed"


def test_multiple_matching_pids_stay_candidate_not_execution_proof(tmp_path):
    proc = _proc(tmp_path)
    other = proc / "1313"
    other.mkdir()
    (other / "stat").write_text(
        "1313 (other) S " + " ".join(["1"] * 18 + ["55555"] + ["1"] * 3)
    )
    (other / "fd").mkdir()
    (other / "fd/1").symlink_to("socket:[424242]")
    r = preflight.collect(proc_root=proc)
    assert len(r["matching_processes"]) == 2
    assert r["provider_execution_attested"] is False
    assert r["observer_signature_verified"] is False


def test_socket_inode_mismatch_cannot_be_attributed(tmp_path):
    proc = _proc(tmp_path)
    (proc / "1212/fd/3").unlink()
    (proc / "1212/fd/3").symlink_to("socket:[999999]")
    r = preflight.collect(proc_root=proc)
    assert r["matching_processes"] == []
    assert r["scan_state"] == "complete"
    assert r["provider_execution_attested"] is False


def test_missing_boot_id_or_net_table_fails_unknown(tmp_path):
    proc = _proc(tmp_path)
    (proc / "net/tcp6").unlink()
    r = preflight.collect(proc_root=proc)
    assert r["scan_state"] == "unknown"
    assert "required procfs record unavailable" in r["observation_unknown_reasons"]
    (proc / "net/tcp6").write_text("header\n")
    (proc / "sys/kernel/random/boot_id").write_text("not-a-boot-id")
    r = preflight.collect(proc_root=proc)
    assert r["scan_state"] == "unknown"
    assert "boot identity unavailable" in r["observation_unknown_reasons"]


def test_pid_budget_is_partial_not_assumed_complete(tmp_path):
    proc = _proc(tmp_path)
    (proc / "1313").mkdir()
    r = preflight.collect(proc_root=proc, max_pids=1)
    assert r["scan_state"] == "partial_unattributed"
    assert "procfs_pid_scan_budget_exceeded" in r["observation_unknown_reasons"]


def test_fd_budget_marks_scan_incomplete_even_if_socket_was_seen(tmp_path):
    proc = _proc(tmp_path)
    (proc / "1212/fd/4").symlink_to("socket:[424242]")
    r = preflight.collect(proc_root=proc, max_fds_per_pid=1)
    assert r["scan_state"] == "partial_unattributed"
    assert r["fd_budget_incomplete_count"] == 1
    assert r["inaccessible_fd_directory_count"] == 0
    assert r["provider_execution_attested"] is False


def test_inventory_is_opt_in_and_loaded_only_reports_residency(tmp_path):
    proc = _proc(tmp_path)
    calls = []

    def get_native(port):
        calls.append(port)
        return {
            "inventory_state": "native_list_authoritative",
            "catalog_count": 2, "resident_model_count": 1,
            "resident_instance_count": 1,
        }

    default = preflight.collect(proc_root=proc, inventory_fetch=get_native)
    assert default["native_inventory"]["inventory_state"] == "not_requested"
    assert calls == []
    enabled = preflight.collect(
        proc_root=proc, probe_native_inventory=True,
        inventory_fetch=get_native,
    )
    assert calls == [1234]
    assert enabled["native_inventory"]["resident_model_count"] == 1
    assert enabled["provider_execution_attested"] is False
    assert enabled["physical_inference_permitted"] is False


def test_unreachable_inventory_is_unknown_without_fallback(tmp_path):
    proc = _proc(tmp_path)

    def denied(_port):
        raise preflight.PreflightUnavailable("native inventory denied")

    r = preflight.collect(
        proc_root=proc, probe_native_inventory=True, inventory_fetch=denied
    )
    assert r["native_inventory"] == {
        "inventory_state": "unknown", "resident_model_count": None
    }
    assert "native inventory denied" in r["observation_unknown_reasons"]


class _FakeReply:
    def __init__(self, raw, status=200):
        self.raw = raw
        self.status = status

    def read(self, length):
        return self.raw[:length]


class _FakeConn:
    def __init__(self, *_args, **_kwargs):
        self.requested = []
        self.reply = _FakeReply(b'{"models":[{"loaded_instances":[]}]}' )

    def request(self, *args, **kwargs):
        self.requested.append((args, kwargs))

    def getresponse(self):
        return self.reply

    def close(self):
        pass


def test_native_probe_one_loopback_get_only(monkeypatch):
    seen = []

    def maker(*args, **kwargs):
        conn = _FakeConn(*args, **kwargs)
        seen.append((args, kwargs, conn))
        return conn

    monkeypatch.setattr(preflight.http.client, "HTTPConnection", maker)
    result = preflight._native_inventory(1234)
    assert result["catalog_count"] == 1
    assert result["resident_model_count"] == 0
    assert seen[0][0] == ("127.0.0.1", 1234)
    assert seen[0][2].requested[0][0][:2] == (
        "GET", "/api/v1/models"
    )


@pytest.mark.parametrize("bad_body", [
    b"{", b'{"models":{}}', b'{"models":[{"key":"loaded?"}]}',
    b"x" * 262145,
])
def test_bad_native_inventory_never_becomes_authoritative(monkeypatch, bad_body):
    class Fake(_FakeConn):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.reply = _FakeReply(bad_body)

    monkeypatch.setattr(preflight.http.client, "HTTPConnection", Fake)
    with pytest.raises(preflight.PreflightUnavailable):
        preflight._native_inventory(1234)


@pytest.mark.parametrize("port,max_pids,max_fds,duration", [
    (0, 10, 10, 1),
    (65536, 10, 10, 1),
    (1234, 0, 10, 1),
    (1234, 10, 513, 1),
    (1234, 10, 10, 20),
])
def test_invalid_probe_budget_denied(port,max_pids,max_fds,duration,tmp_path):
    with pytest.raises(ValueError):
        preflight.collect(
            proc_root=tmp_path, port=port, max_pids=max_pids,
            max_fds_per_pid=max_fds, max_duration_seconds=duration,
        )
