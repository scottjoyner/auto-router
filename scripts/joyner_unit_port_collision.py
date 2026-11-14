#!/usr/bin/env python3
"""Read-only Joyner duplicate-model-unit / port-ownership evidence.

The observer NEVER calls start/stop/disable/reload, reads model bytes, makes an
inference HTTP request, or prints ExecStart arguments / Environment variables.
A reported collision is a configuration + runtime hypothesis, not permission
to change a service. The exit status is always HOLD (78).
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import socket
import subprocess
from pathlib import Path

SCHEMA = "auto-router.joyner-unit-port-collision.v1"
CANONICAL = {"fleet-joyner-gpu": 1234, "fleet-joyner-ling": 1236}
LEGACY = {"joyner-lane-1234": 1234, "joyner-lane-1236": 1236}
EXPECTED = CANONICAL | LEGACY
ALLOWED_PORTS = frozenset((1234, 1236))


def _call(args: list[str]) -> tuple[int, str]:
    """Never return stderr or include unchecked command args in evidence."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=5, check=False
        )
        return result.returncode, result.stdout[:65_536]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 1, ""


def declared_ports(unit_text: str) -> list[int]:
    """Project only numeric --port values; never surface arbitrary ExecStart."""
    ports = []
    for line in unit_text.splitlines():
        if not line.startswith("ExecStart="):
            continue
        try:
            argv = shlex.split(line.partition("=")[2])
        except ValueError:
            return []
        if not argv or Path(argv[0]).name != "llama-server":
            return []
        for index, token in enumerate(argv):
            if token in ("--port", "-p") and index + 1 < len(argv):
                candidate = argv[index + 1]
            elif token.startswith("--port="):
                candidate = token.partition("=")[2]
            else:
                continue
            if candidate.isascii() and candidate.isdecimal():
                port = int(candidate)
                if 1 <= port <= 65535:
                    ports.append(port)
    return sorted(ports)


def parse_properties(raw: str) -> dict[str, str]:
    allowed = {
        "FragmentPath", "LoadState", "ActiveState", "SubState",
        "Result", "NRestarts", "ExecMainStatus", "MainPID",
    }
    props = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in allowed:
            props[key] = value.strip()
    return props


def read_unit(name: str, expected_port: int, *,
              caller=_call, file_reader=None) -> dict:
    if name not in EXPECTED or expected_port != EXPECTED[name]:
        raise ValueError("unapproved_unit_name_or_port")
    file_reader = file_reader or (lambda p: Path(p).read_bytes())
    rc, raw = caller([
        "systemctl", "--user", "show", name + ".service",
        "-p", "FragmentPath", "-p", "LoadState", "-p", "ActiveState",
        "-p", "SubState", "-p", "Result", "-p", "NRestarts",
        "-p", "ExecMainStatus", "-p", "MainPID", "--no-pager",
    ])
    props = parse_properties(raw) if rc == 0 else {}
    fragment = props.get("FragmentPath", "")
    unit_hash = None
    ports: list[int] = []
    try:
        if fragment and Path(fragment).is_absolute():
            data = file_reader(fragment)
            if isinstance(data, bytes) and len(data) <= 128 * 1024:
                unit_hash = hashlib.sha256(data).hexdigest()
                ports = declared_ports(data.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        pass
    restart_raw = props.get("NRestarts", "")
    restart_count = int(restart_raw) if restart_raw.isdecimal() else None
    # No full file paths, model paths, credentials, argv, stdout, or stderr.
    return {
        "unit": name,
        "expected_port": expected_port,
        "declared_ports": ports,
        "fragment_sha256": unit_hash,
        "load": props.get("LoadState", "unverified"),
        "active": props.get("ActiveState", "unverified"),
        "sub": props.get("SubState", "unverified"),
        "result": props.get("Result", "unverified"),
        "exit_code": (
            int(props["ExecMainStatus"]) if props.get("ExecMainStatus", "").isdecimal()
            else None
        ),
        "restart_count": restart_count,
        "main_pid": (
            int(props["MainPID"]) if props.get("MainPID", "").isdecimal()
            else None
        ),
    }


def read_port_listeners(caller=_call) -> tuple[dict[int, list[int]], bool]:
    rc, raw = caller(["ss", "-H", "-ltnp"])
    if rc != 0:
        return {}, False
    listeners: dict[int, list[int]] = {port: [] for port in ALLOWED_PORTS}
    for line in raw.splitlines():
        fields = line.split()
        if not fields or fields[0] != "LISTEN" or len(fields) < 5:
            continue
        local = fields[3]
        try:
            port = int(local.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            continue
        if port not in ALLOWED_PORTS:
            continue
        # PID is useful for matching a unit MainPID, but no command line is emitted.
        pids = [int(p) for p in re.findall(r"\bpid=(\d+)\b", line)]
        listeners[port].extend(pid for pid in pids if pid not in listeners[port])
    return listeners, True


def assess(rows: list[dict], listeners: dict[int, list[int]], *,
           correct_host: bool, socket_inventory_ok: bool) -> dict:
    by_name = {r.get("unit"): r for r in rows if r.get("unit") in EXPECTED}
    reasons = []
    if not correct_host:
        reasons.append("observer_not_joyner")
    if not socket_inventory_ok:
        reasons.append("socket_inventory_unavailable")
    if set(by_name) != set(EXPECTED):
        reasons.append("unit_inventory_incomplete")
    for name, expected in EXPECTED.items():
        row = by_name.get(name, {})
        if row.get("declared_ports") != [expected] or not row.get("fragment_sha256"):
            reasons.append("unit_port_declaration_unverified:" + name)
    pairs = []
    for canonical, legacy in (
        ("fleet-joyner-gpu", "joyner-lane-1234"),
        ("fleet-joyner-ling", "joyner-lane-1236"),
    ):
        port = EXPECTED[canonical]
        current, old = by_name.get(canonical, {}), by_name.get(legacy, {})
        duplicated = (
            current.get("declared_ports") == [port]
            and old.get("declared_ports") == [port]
        )
        active_pid = current.get("main_pid")
        owned = (
            current.get("active") == "active"
            and isinstance(active_pid, int) and active_pid > 0
            and active_pid in listeners.get(port, [])
        )
        looping = (
            old.get("sub") == "auto-restart"
            and old.get("result") == "exit-code"
            and (old.get("restart_count") or 0) > 0
        )
        pairs.append({
            "port": port,
            "canonical": canonical,
            "legacy": legacy,
            "same_declared_port": duplicated,
            "canonical_listener_pid_matched": owned,
            "legacy_restart_loop": looping,
            "possible_port_collision": duplicated and owned and looping,
            "confirmed_exit_cause": False,
        })
        if duplicated:
            reasons.append("overlapping_unit_port_declarations:" + str(port))
        if looping:
            reasons.append("legacy_unit_restarting:" + legacy)
        if not owned:
            reasons.append("canonical_port_owner_unverified:" + str(port))
    return {
        "schema": SCHEMA,
        "verdict": "HOLD",
        "observer_host_verified": correct_host,
        "read_only": True,
        "credentials_accessed": False,
        "model_request_sent": False,
        "journal_cause_verified": False,
        "may_disable_services": False,
        "may_change_router": False,
        "may_allocate_gpu": False,
        "reasons": sorted(set(reasons)),
        "ports": pairs,
        "units": rows,
    }


def audit(*, hostname: str | None = None, caller=_call, file_reader=None) -> dict:
    local = (hostname or socket.gethostname()).split(".", 1)[0].lower()
    if local != "joyner":
        return assess([], {}, correct_host=False, socket_inventory_ok=False)
    rows = [
        read_unit(name, port, caller=caller, file_reader=file_reader)
        for name, port in EXPECTED.items()
    ]
    listeners, okay = read_port_listeners(caller)
    return assess(rows, listeners, correct_host=True, socket_inventory_ok=okay)


def main() -> int:
    print(json.dumps(audit(), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
