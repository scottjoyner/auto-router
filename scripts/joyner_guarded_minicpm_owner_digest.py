#!/usr/bin/env python3
"""Owner-only, read-only guarded MiniCPM disk/model-process custody.

No network requests, API key access, model generation, service changes or
cutover approval. Run as the existing guarded :18320 process owner on Joyner.
The JSON receipt always returns HOLD (78), even if all digest checks pass.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
from pathlib import Path

SCHEMA = "auto-router.joyner-guarded-minicpm-owner-digest.v1"
PORT = 18320
PINNED_BYTES = 688_065_920
PINNED_SHA256 = "81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa"
APPROVED_HOME = Path("/home/joyner")


def _unique_flag(argv: list[str], flags: tuple[str, ...]) -> str | None:
    values = [argv[i + 1] for i in range(len(argv) - 1) if argv[i] in flags]
    return values[0] if len(values) == 1 else None


def _starttime(proc: Path) -> str:
    # Field 22 in /proc/<pid>/stat, after the parenthesized comm field.
    fields = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
    if len(fields) <= 19 or not fields[19].isdigit():
        raise ValueError("invalid_pid_starttime")
    return fields[19]


def _processes(proc_root: Path, euid: int) -> list[tuple[int, str, Path]]:
    found = []
    for p in proc_root.iterdir():
        if not p.name.isdecimal():
            continue
        try:
            if (p / "comm").read_text().strip() != "llama-server":
                continue
            # A different owner cannot serve as this owner's custody proof.
            # Do not inspect the other owner's argv or key-file path.
            uid_line = next(x for x in (p / "status").read_text().splitlines()
                            if x.startswith("Uid:"))
            uids = [int(x) for x in uid_line.partition(":")[2].split()]
            if len(uids) != 4 or uids[0] != euid or uids[1] != euid:
                continue
            argv = [x.decode("utf-8", "replace") for x in
                    (p / "cmdline").read_bytes().split(b"\0") if x]
            if not argv or Path(argv[0]).name != "llama-server":
                return []
            if _unique_flag(argv, ("--port",)) != str(PORT):
                continue
            model = _unique_flag(argv, ("-m", "--model"))
            key_file = _unique_flag(argv, ("--api-key-file",))
            if not model or not key_file:
                raise ValueError("guarded_process_flags_missing")
            found.append((int(p.name), _starttime(p), Path(model)))
        except (OSError, ValueError, StopIteration, PermissionError):
            # Inaccessible unrelated processes are ignored; an inaccessible
            # candidate owned by the observer cannot establish custody.
            return []
    return found


def _allowed_path(path: Path, home: Path) -> bool:
    if (not path.is_absolute() or ".." in path.parts or not home.is_absolute()
            or path == home or not path.is_relative_to(home)):
        return False
    current = path
    while current != home:
        if current.is_symlink():
            return False
        current = current.parent
    return not home.is_symlink()


def hash_file(path: Path, home: Path, euid: int, *,
              expected_bytes: int = PINNED_BYTES,
              expected_sha256: str = PINNED_SHA256) -> bool:
    """No-follow file descriptor with before/after fstat and path identity."""
    if not _allowed_path(path, home):
        return False
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(fd)
        expected_identity = (before.st_dev, before.st_ino, before.st_size,
                             before.st_mtime_ns, before.st_ctime_ns)
        if (not stat.S_ISREG(before.st_mode) or before.st_size != expected_bytes
                or before.st_uid != euid):
            return False
        digest = hashlib.sha256()
        while chunk := os.read(fd, 4 * 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        final_path = path.lstat()
        final_identity = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        return (expected_identity == final_identity
                and (final_path.st_dev, final_path.st_ino) ==
                    (before.st_dev, before.st_ino)
                and not stat.S_ISLNK(final_path.st_mode)
                and digest.hexdigest() == expected_sha256)
    except (OSError, ValueError):
        return False
    finally:
        if fd >= 0:
            os.close(fd)


def audit(*, hostname: str | None = None, euid: int | None = None,
          proc_root: Path = Path("/proc"), home: Path = APPROVED_HOME,
          hasher=hash_file) -> dict:
    actual_host = (hostname or socket.gethostname()).split(".", 1)[0].lower()
    actual_uid = os.geteuid() if euid is None else euid
    host_ok = actual_host == "joyner"
    owner_ok = host_ok and actual_uid >= 1000  # Never run as root.
    pid = None
    process_verified = False
    digest_verified = False
    if owner_ok:
        try:
            before = _processes(proc_root, actual_uid)
            if len(before) == 1 and _allowed_path(before[0][2], home):
                pid = before[0][0]
                digest_verified = bool(hasher(before[0][2], home, actual_uid))
                # Fail closed on service restart or model/owner changes during hash.
                after = _processes(proc_root, actual_uid)
                process_verified = len(after) == 1 and before == after
                digest_verified = digest_verified and process_verified
        except (OSError, ValueError, PermissionError):
            pass
    return {
        "schema": SCHEMA, "verdict": "HOLD", "read_only": True,
        "guarded_port": PORT, "observer_host_verified": host_ok,
        "nonroot_observer_verified": owner_ok,
        "exact_guarded_process_owner_and_identity_verified": process_verified,
        "guarded_process_pid": pid if process_verified else None,
        "guarded_disk_model_matches_pinned_sha256": digest_verified,
        "resident_gpu_weights_verified": False,
        "request_contract_equivalence_verified": False,
        "file_hash_receipt_is_signed": False,
        "credential_values_read": False, "environment_files_read": False,
        "model_request_sent": False, "inference_generated": False,
        "router_cutover_authorized": False, "service_change_authorized": False,
    }


def main() -> int:
    print(json.dumps(audit(), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
