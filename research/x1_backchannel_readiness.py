#!/usr/bin/env python3
"""Read-only authenticated SSH backchannel check for the x1 fleet controller.

No privileged commands, service changes, remote script execution, or writes.
Successful SSH reachability is NOT an out-of-band recovery console.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
from collections.abc import Callable
from typing import Any

TARGETS = {"x1-370": "x1-370"}
REMOTE_QUERY = "hostname; uname -r"
HOSTNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,100}$")
KERNEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{1,100}$")


def make_argv(target: str) -> list[str]:
    if target not in TARGETS:
        raise ValueError("Unapproved target alias")
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "ConnectTimeout=3",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "RequestTTY=no",
        "-o",
        "ClearAllForwardings=yes",
        target,
        REMOTE_QUERY,
    ]


def probe(
    target: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scope": "authenticated_read_only_ssh_backchannel",
        "source_hostname": socket.gethostname(),
        "target": target,
        "reachable": False,
        "host_identity_matched": False,
        "may_elevate_privileges": False,
        "may_repair_os": False,
        "may_reboot": False,
        "out_of_band_console_verified": False,
        "reason": "unverified",
    }
    try:
        argv = make_argv(target)
        process = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=7,
        )
        if process.returncode != 0:
            result["reason"] = "ssh_unavailable_or_untrusted"
            return result
        lines = process.stdout.strip().splitlines()
        if len(lines) != 2:
            result["reason"] = "unexpected_remote_reply"
            return result
        hostname, kernel = lines
        if not HOSTNAME_PATTERN.fullmatch(hostname) or not KERNEL_PATTERN.fullmatch(kernel):
            result["reason"] = "invalid_remote_identity_fields"
            return result
        result["host_identity_matched"] = hostname == TARGETS[target]
        if not result["host_identity_matched"]:
            result["reason"] = "remote_hostname_mismatch"
            return result
        result["kernel"] = kernel
        result["reachable"] = True
        result["reason"] = "authenticated_read_only_ssh_reachable"
        return result
    except ValueError:
        result["reason"] = "target_not_allowlisted"
    except subprocess.TimeoutExpired:
        result["reason"] = "ssh_probe_timeout"
    except OSError:
        result["reason"] = "ssh_command_unavailable"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), default="x1-370")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = probe(args.target)
    print(json.dumps(report, sort_keys=True))
    return 2 if args.check and not report["reachable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
