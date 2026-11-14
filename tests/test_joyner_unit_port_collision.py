"""Offline Joyner service-port collision witness tests.

Fake systemd unit text includes a synthetic credential canary; the witness must
never surface it. No SSH, systemctl, journal, GPU, or network calls are made.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from scripts import joyner_unit_port_collision as audit


SECRET = "synthetic-secret-DO-NOT-EMIT"


def unit_text(port, restart="always"):
    return (
        "[Unit]\nDescription=Synthetic\n[Service]\n"
        "ExecStart=/fake/bin/llama-server --host 0.0.0.0 "
        f"--port {port} --api-key {SECRET} --model /private/synthetic.gguf\n"
        f"Restart={restart}\nRestartSec=3\n"
        f"Environment=PRIVATE_API_KEY={SECRET}\n"
    ).encode()


def fixture():
    data = {unit: unit_text(port) for unit, port in audit.EXPECTED.items()}
    info = {
        "fleet-joyner-gpu": ("active", "running", "success", "0", "0", "111"),
        "fleet-joyner-ling": ("active", "running", "success", "0", "0", "222"),
        "joyner-lane-1234": ("activating", "auto-restart", "exit-code", "459", "1", "0"),
        "joyner-lane-1236": ("inactive", "dead", "success", "0", "0", "0"),
    }
    calls = []

    def caller(args):
        calls.append(args)
        assert args[0] in ("systemctl", "ss")
        if args[0] == "ss":
            return 0, (
                'LISTEN 0 512 0.0.0.0:1234 0.0.0.0:* '
                'users:(("llama-server",pid=111,fd=7))\n'
                'LISTEN 0 512 0.0.0.0:1236 0.0.0.0:* '
                'users:(("llama-server",pid=222,fd=7))\n'
                'LISTEN 0 512 127.0.0.1:19175 0.0.0.0:* '
                'users:(("llama-server",pid=333,fd=7))\n'
            )
        name = args[3].removesuffix(".service")
        active, sub, result, restarts, exit_status, pid = info[name]
        return 0, (
            f"FragmentPath=/synthetic/systemd/{name}.service\n"
            "LoadState=loaded\n"
            f"ActiveState={active}\nSubState={sub}\nResult={result}\n"
            f"NRestarts={restarts}\nExecMainStatus={exit_status}\n"
            f"MainPID={pid}\n"
        )

    def reader(path):
        return data[path.rsplit("/", 1)[-1].removesuffix(".service")]

    return caller, reader, calls


def test_declaration_extracts_only_port_and_no_credentials():
    raw = unit_text(1234).decode()
    assert audit.declared_ports(raw) == [1234]
    assert SECRET not in str(audit.declared_ports(raw))
    assert audit.declared_ports(
        "ExecStart=/fake/bin/llama-server --port=1236 --api-key " + SECRET
    ) == [1236]
    assert audit.declared_ports(
        "ExecStart=/fake/bin/llama-server -p 1236 --api-key " + SECRET
    ) == [1236]


@pytest.mark.parametrize("line", [
    "ExecStart=/fake/bin/bash -c 'llama-server --port 1234'",
    "ExecStart=/fake/bin/llama-server --port not-a-port",
    "ExecStart=/fake/bin/llama-server --port 65536",
    "ExecStart=/fake/bin/llama-server --port=not-a-number",
    "ExecStart=/fake/bin/llama-server --port 'unclosed",
    "ExecStart=/fake/bin/llama-server --model /private/model.gguf",
])
def test_ambiguous_port_declaration_never_passes(line):
    assert audit.declared_ports(line) == []


def test_declared_port_duplicate_is_not_trusted():
    assert audit.declared_ports(
        "ExecStart=/fake/bin/llama-server --port 1234 --port=1234"
    ) == [1234, 1234]


def test_sanitized_units_and_listener_evidence():
    caller, reader, calls = fixture()
    rows = [
        audit.read_unit(u, port, caller=caller, file_reader=reader)
        for u, port in audit.EXPECTED.items()
    ]
    ports, valid = audit.read_port_listeners(caller)
    assert valid
    assert ports == {1234: [111], 1236: [222]}
    report = audit.assess(rows, ports, correct_host=True, socket_inventory_ok=True)
    assert report["verdict"] == "HOLD"
    assert report["journal_cause_verified"] is False
    assert report["ports"][0]["possible_port_collision"]
    assert report["ports"][1]["same_declared_port"]
    assert report["ports"][1]["possible_port_collision"] is False
    assert "legacy_unit_restarting:joyner-lane-1234" in report["reasons"]
    assert report["may_disable_services"] is False
    assert report["may_allocate_gpu"] is False
    serialized = json.dumps(report)
    assert SECRET not in serialized
    assert "/private/" not in serialized
    assert "Environment=" not in serialized
    assert all(x[0] in ("systemctl", "ss") for x in calls)


def test_wrong_host_invokes_no_observers():
    def forbidden(*args, **kwargs):
        raise AssertionError("observer called on wrong host")
    report = audit.audit(hostname="x1-370", caller=forbidden,
                         file_reader=forbidden)
    assert report["verdict"] == "HOLD"
    assert report["observer_host_verified"] is False
    assert report["units"] == []


def test_correct_host_uses_only_whitelisted_read_only_calls():
    caller, reader, calls = fixture()
    report = audit.audit(hostname="joyner", caller=caller, file_reader=reader)
    assert report["observer_host_verified"] is True
    assert len(calls) == 5
    assert sum(x[0] == "systemctl" for x in calls) == 4
    assert sum(x[0] == "ss" for x in calls) == 1
    assert not any(
        forbidden in " ".join(arg) for arg in calls
        for forbidden in ("stop", "restart", "disable", "enable", "daemon-reload",
                          "journalctl", "curl", "ssh", "sudo")
    )


def test_absent_or_failed_socket_census_is_hold():
    caller, reader, _ = fixture()
    rows = [audit.read_unit(u, p, caller=caller, file_reader=reader)
            for u, p in audit.EXPECTED.items()]
    report = audit.assess(rows, {}, correct_host=True, socket_inventory_ok=False)
    assert report["verdict"] == "HOLD"
    assert "socket_inventory_unavailable" in report["reasons"]
    assert not report["ports"][0]["canonical_listener_pid_matched"]


def test_missing_unit_contents_never_prove_collision():
    caller, reader, _ = fixture()
    row = audit.read_unit(
        "fleet-joyner-gpu", 1234, caller=caller, file_reader=lambda _: b""
    )
    assert row["declared_ports"] == []
    assert row["fragment_sha256"] is not None
    result = audit.assess([row], {}, correct_host=True, socket_inventory_ok=False)
    assert result["verdict"] == "HOLD"
    assert "unit_inventory_incomplete" in result["reasons"]


@pytest.mark.parametrize("name,port", [
    ("joyner-lane-1234", 1236),
    ("unapproved", 1234),
])
def test_unapproved_unit_lookup_refused(name, port):
    with pytest.raises(ValueError, match="unapproved"):
        audit.read_unit(name, port)


def test_non_numeric_restart_and_unknown_pid_are_not_invented():
    props = audit.parse_properties(
        "NRestarts=unknown\nMainPID=not-reported\nActiveState=active\n"
        "Environment=MY_TOKEN=SYNTHETIC-SECRET\n"
    )
    assert "Environment" not in props
    assert props["NRestarts"] == "unknown"


def test_main_issues_hold_without_running_commands(monkeypatch, capsys):
    monkeypatch.setattr(
        audit, "audit",
        lambda: {"schema": audit.SCHEMA, "verdict": "HOLD",
                 "may_disable_services": False, "read_only": True},
    )
    assert audit.main() == 78
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["may_disable_services"] is False


def test_failed_systemctl_read_does_not_claim_verified_unit():
    row = audit.read_unit(
        "joyner-lane-1234", 1234,
        caller=lambda args: (1, ""),
    )
    assert row["declared_ports"] == []
    assert row["active"] == "unverified"
    assert row["fragment_sha256"] is None
