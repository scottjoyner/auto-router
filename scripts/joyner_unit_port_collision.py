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
DEPENDENT_PROXIES = (
    "joyner-lane-1234-lan-proxy",
    "joyner-lane-1234-ts-proxy",
)


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
        "UnitFileState", "Requires", "After",
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
        unit_path = Path(fragment)
        allowed_parent = Path.home() / ".config" / "systemd" / "user"
        # Do not follow a systemd fragment to an arbitrary private file.
        if (unit_path.is_absolute() and unit_path.parent == allowed_parent
                and unit_path.name == name + ".service"
                and not unit_path.is_symlink()):
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


def read_dependent_proxy(name: str, *, caller=_call, file_reader=None) -> dict:
    """Read only the two known proxy units and emit boolean dependency facts.

    The unit definition may contain sensitive arguments. It is inspected
    transiently for references, hashed, and NEVER included in the result.
    """
    if name not in DEPENDENT_PROXIES:
        raise ValueError("unapproved_proxy_unit")
    file_reader = file_reader or (lambda path: Path(path).read_bytes())
    rc, raw = caller([
        "systemctl", "--user", "show", name + ".service",
        "-p", "FragmentPath", "-p", "LoadState", "-p", "ActiveState",
        "-p", "SubState", "-p", "UnitFileState", "-p", "Requires",
        "-p", "After", "--no-pager",
    ])
    props = parse_properties(raw) if rc == 0 else {}
    fragment_digest = None
    static_requires_legacy = False
    static_after_legacy = False
    launch_mentions_1234 = False
    launch_mentions_loopback = False
    try:
        unit_path = Path(props.get("FragmentPath", ""))
        allowed_parent = Path.home() / ".config" / "systemd" / "user"
        if (unit_path.is_absolute() and unit_path.parent == allowed_parent
                and unit_path.name == name + ".service"
                and not unit_path.is_symlink()):
            data = file_reader(str(unit_path))
            if isinstance(data, bytes) and len(data) <= 128 * 1024:
                fragment_digest = hashlib.sha256(data).hexdigest()
                text = data.decode("utf-8")
                lines = text.splitlines()
                static_requires_legacy = any(
                    "joyner-lane-1234.service" in line.partition("=")[2].split()
                    for line in lines if line.startswith("Requires=")
                )
                static_after_legacy = any(
                    "joyner-lane-1234.service" in line.partition("=")[2].split()
                    for line in lines if line.startswith("After=")
                )
                commands = [line.partition("=")[2] for line in lines
                            if line.startswith("ExecStart=")]
                launch_mentions_1234 = any("1234" in line for line in commands)
                launch_mentions_loopback = any(
                    "127.0.0.1" in line or "localhost" in line
                    for line in commands
                )
    except (OSError, UnicodeError, ValueError):
        pass
    # Only fixed-unit dependency facts may be emitted; never print arbitrary
    # Requires/After values or raw ExecStart, even on error.
    return {
        "unit": name,
        "load": props.get("LoadState", "unverified"),
        "active": props.get("ActiveState", "unverified"),
        "sub": props.get("SubState", "unverified"),
        "unit_file_state": props.get("UnitFileState", "unverified"),
        "fragment_sha256": fragment_digest,
        "runtime_requires_legacy": (
            "joyner-lane-1234.service" in props.get("Requires", "").split()
        ),
        "runtime_after_legacy": (
            "joyner-lane-1234.service" in props.get("After", "").split()
        ),
        "static_requires_legacy": static_requires_legacy,
        "static_after_legacy": static_after_legacy,
        "launch_mentions_port_1234": launch_mentions_1234,
        "launch_mentions_loopback": launch_mentions_loopback,
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
           correct_host: bool, socket_inventory_ok: bool,
           dependent_proxies: list[dict] | None = None) -> dict:
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
    proxy_rows = dependent_proxies if dependent_proxies is not None else []
    proxy_by_name = {p.get("unit"): p for p in proxy_rows
                     if p.get("unit") in DEPENDENT_PROXIES}
    if set(proxy_by_name) != set(DEPENDENT_PROXIES):
        reasons.append("dependent_proxy_inventory_incomplete")
    for name in DEPENDENT_PROXIES:
        proxy = proxy_by_name.get(name, {})
        if (not proxy.get("fragment_sha256")
                or proxy.get("load") != "loaded"):
            reasons.append("dependent_proxy_source_unverified:" + name)
        if (proxy.get("runtime_requires_legacy")
                or proxy.get("static_requires_legacy")):
            reasons.append("legacy_proxy_requires_legacy_unit:" + name)
        if (proxy.get("unit_file_state") == "enabled"):
            reasons.append("legacy_proxy_enabled:" + name)
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
        # Unit file bytes are inspected for ports and hashes. They MAY
        # contain sensitive launch arguments; never claim they were unread.
        "unit_definitions_inspected_for_ports": correct_host,
        "credential_values_emitted": False,
        "environment_files_opened": False,
        "model_request_sent": False,
        "journal_cause_verified": False,
        "may_disable_services": False,
        "may_retire_legacy_unit": False,
        "dependent_proxy_owner_verified": False,
        "dependent_proxy_contract_verified": False,
        "may_change_router": False,
        "may_allocate_gpu": False,
        "reasons": sorted(set(reasons)),
        "ports": pairs,
        "units": rows,
        "dependent_proxies": [proxy_by_name.get(name, {"unit": name, "load": "unverified"})
                              for name in DEPENDENT_PROXIES],
    }


def audit(*, hostname: str | None = None, caller=_call, file_reader=None) -> dict:
    local = (hostname or socket.gethostname()).split(".", 1)[0].lower()
    if local != "joyner":
        return assess([], {}, correct_host=False, socket_inventory_ok=False)
    rows = [
        read_unit(name, port, caller=caller, file_reader=file_reader)
        for name, port in EXPECTED.items()
    ]
    proxies = [
        read_dependent_proxy(name, caller=caller, file_reader=file_reader)
        for name in DEPENDENT_PROXIES
    ]
    listeners, okay = read_port_listeners(caller)
    return assess(rows, listeners, correct_host=True, socket_inventory_ok=okay,
                  dependent_proxies=proxies)


def main() -> int:
    print(json.dumps(audit(), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
