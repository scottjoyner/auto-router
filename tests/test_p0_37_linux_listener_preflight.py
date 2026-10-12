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
    assert "process_or_listener_changed_during_snapshot" in r["observation_unknown_reasons"]
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
    b'{"models":[],"models":[{"loaded_instances":[{"id":"fake"}]}]}',
    b'{"models":[{"loaded_instances":[{}]}]}',
    b'{"models":[{"loaded_instances":[{"id":123}]}]}',
    b'{"models":[{"loaded_instances":[{"id":""}]}]}',
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



def test_bounded_observation_no_ambient_credential_or_network_by_default(
    tmp_path, monkeypatch,
):
    proc = _proc(tmp_path)
    network_attempts = []

    def fail_network(_port):
        network_attempts.append(_port)
        raise AssertionError("no GET request authorized by default")

    # Even with an inventory transport override present, the default is
    # local procfs only and cannot issue any provider HTTP request.
    result = preflight.collect(
        proc_root=proc,
        inventory_fetch=fail_network,
        probe_native_inventory=False,
    )
    assert network_attempts == []
    assert result["native_inventory"]["inventory_state"] == "not_requested"
    assert result["operator_approved"] is False


def test_procfs_tcp_row_malformed_is_unknown_not_empty(tmp_path):
    proc = _proc(tmp_path)
    (proc / "net/tcp").write_text(
        "header\n0: BAD:HELLO 00000000:0000 0A x x x x x x\n"
    )
    r = preflight.collect(proc_root=proc)
    assert r["listener_state"] == "unknown"
    assert r["scan_state"] == "unknown"
    assert r["provider_execution_attested"] is False



def test_listener_socket_disappears_during_repeat_snapshot(tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    seen = iter([{"424242"}, set()])
    monkeypatch.setattr(
        preflight, "_listener_inodes", lambda _root, _port: next(seen)
    )
    result = preflight.collect(proc_root=proc)
    assert result["listener_state"] == "listening"
    assert result["matching_processes"] == []
    assert "process_or_listener_changed_during_snapshot" in result[
        "observation_unknown_reasons"
    ]
    assert result["provider_execution_attested"] is False


def test_fd_disappears_on_second_ownership_read(tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    seen = iter([(True, "complete"), (False, "complete")])
    monkeypatch.setattr(
        preflight, "_visible_fd_socket", lambda *_args: next(seen)
    )
    result = preflight.collect(proc_root=proc)
    assert result["matching_processes"] == []
    assert result["scan_state"] == "partial_unattributed"


def test_procfs_listener_table_bounded_even_with_repeated_records(tmp_path):
    proc = _proc(tmp_path)
    (proc / "net/tcp").write_bytes(b"x" * 262145)
    outcome = preflight.collect(proc_root=proc)
    assert outcome["listener_state"] == "unknown"
    assert outcome["observation_unknown_reasons"] == [
        "procfs record exceeds bounded read"
    ]



def test_target_pid_attributed_without_scanning_unrelated_processes(tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    other = proc / "1357"
    other.mkdir()
    # An unrelated inaccessible process must not affect targeted mode.
    visited = []
    real_scandir = preflight.os.scandir

    def audit_scandir(path):
        visited.append(str(path))
        if str(path) == str(proc):
            raise AssertionError("target PID must not enumerate unrelated processes")
        return real_scandir(path)

    monkeypatch.setattr(preflight.os, "scandir", audit_scandir)
    result = preflight.collect(proc_root=proc, target_pid=1212)
    assert result["scan_mode"] == "targeted_pid"
    assert result["target_pid"] == 1212
    assert result["scan_state"] == "targeted_attributed"
    assert result["scanned_pid_count"] == 1
    assert len(result["matching_processes"]) == 1
    assert result["inaccessible_fd_directory_count"] == 0
    assert str(proc / "1212" / "fd") in visited
    assert str(proc) not in visited
    assert result["physical_inference_permitted"] is False
    assert result["provider_execution_attested"] is False


def test_target_pid_not_socket_owner_is_not_global_no_owner(tmp_path):
    proc = _proc(tmp_path)
    other = proc / "1357"
    other.mkdir()
    (other / "fd").mkdir()
    result = preflight.collect(proc_root=proc, target_pid=1357)
    assert result["scan_mode"] == "targeted_pid"
    assert result["scan_state"] == "target_not_attributed"
    assert result["scanned_pid_count"] == 1
    assert result["listener_socket_count"] == 1
    assert result["matching_processes"] == []


def test_target_missing_pid_is_unknown_not_no_owner(tmp_path):
    proc = _proc(tmp_path)
    result = preflight.collect(proc_root=proc, target_pid=445566)
    assert result["scan_state"] == "partial_unattributed"
    assert result["inaccessible_fd_directory_count"] == 1
    assert "some_process_fd_directories_inaccessible" in result[
        "observation_unknown_reasons"
    ]


@pytest.mark.parametrize("target", [0, -1, 2**31, True, "1234"])
def test_unsafe_target_pid_rejected_without_procfs_io(tmp_path, target):
    with pytest.raises(ValueError, match="target PID"):
        preflight.collect(proc_root=tmp_path, target_pid=target)


def test_targeted_pid_still_requires_same_process_start_and_live_socket(
    tmp_path, monkeypatch,
):
    proc = _proc(tmp_path)
    samples = iter([100, 101])
    monkeypatch.setattr(
        preflight, "_start_ticks", lambda _proc, _pid: next(samples)
    )
    result = preflight.collect(proc_root=proc, target_pid=1212)
    assert result["scan_state"] == "partial_unattributed"
    assert result["matching_processes"] == []
    assert "process_or_listener_changed_during_snapshot" in result[
        "observation_unknown_reasons"
    ]
