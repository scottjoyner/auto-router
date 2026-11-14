#!/usr/bin/env python3
"""Read-only pinned canonical MiniCPM model-file identity at Joyner :1234.

Cross-checked against the existing source-pinned guarded-canary reference.
This is disk file evidence only, not a proof of resident GPU weights or
authorization to use/retire the service. Never reads credentials or performs
model requests. Always exits HOLD 78.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
from pathlib import Path

SCHEMA = "auto-router.joyner-minicpm-source-pin.v1"
HOSTNAME = "joyner"
SOURCE_PORT = 1234
MODEL = Path("/home/scott/fleet-gpu/models/MiniCPM5-1B-Q4_K_M.gguf")
EXPECTED_BYTES = 688_065_920
EXPECTED_SHA256 = "81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa"
CHUNK_BYTES = 4 * 1024 * 1024


def _arg(argv: list[str], names: tuple[str, ...]) -> str | None:
    values = [
        argv[i + 1] for i in range(len(argv) - 1)
        if argv[i] in names
    ]
    return values[0] if len(values) == 1 else None


def process_binding(proc_root: Path = Path("/proc"),
                    model_path: Path = MODEL) -> tuple[bool, int | None, str]:
    """Require exactly one same-owner llama-server using the pinned model."""
    if model_path != MODEL:
        return False, None, "noncanonical_model_path"
    found = []
    try:
        pids = [p for p in proc_root.iterdir() if p.name.isdecimal()]
    except OSError:
        return False, None, "process_inventory_unavailable"
    for entry in pids:
        try:
            argv = [
                piece.decode("utf-8", "replace")
                for piece in (entry / "cmdline").read_bytes().split(b"\0") if piece
            ]
            if not argv or Path(argv[0]).name != "llama-server":
                continue
            if _arg(argv, ("--port",)) != str(SOURCE_PORT):
                continue
            model = _arg(argv, ("--model", "-m"))
            if model != str(MODEL):
                return False, None, "source_process_model_path_mismatch"
            uid_row = next(
                row for row in (entry / "status").read_text().splitlines()
                if row.startswith("Uid:")
            )
            real_uid = int(uid_row.split()[1])
            if real_uid != os.geteuid():
                return False, None, "source_process_owner_mismatch"
            found.append(int(entry.name))
        except (OSError, ValueError, StopIteration, IndexError):
            continue
    if len(found) != 1:
        return False, None, "source_process_not_unique"
    return True, found[0], "source_process_bound"


def verify_file(path: Path = MODEL, *, expected_size: int = EXPECTED_BYTES,
                expected_sha256: str = EXPECTED_SHA256) -> dict:
    """Hash an approved fixed regular file, never a symlink or key path."""
    if path != MODEL:
        return {"file_verified": False, "reason": "noncanonical_model_path"}
    try:
        if path.is_symlink():
            raise ValueError("source_model_symlink")
        before = path.stat()
        if (not stat.S_ISREG(before.st_mode)
                or before.st_size != expected_size
                or before.st_uid != os.geteuid()):
            raise ValueError("source_model_stat_mismatch")
        digest = hashlib.sha256()
        with path.open("rb", buffering=1024 * 1024) as handle:
            for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(chunk)
        after = path.stat()
        identity = lambda s: (
            s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
        )
        if identity(before) != identity(after):
            raise ValueError("source_model_changed_during_hash")
        if digest.hexdigest() != expected_sha256:
            raise ValueError("source_model_digest_mismatch")
        return {"file_verified": True, "reason": "pinned_source_file_verified"}
    except (OSError, ValueError) as exc:
        safe = {
            "source_model_symlink",
            "source_model_stat_mismatch",
            "source_model_changed_during_hash",
            "source_model_digest_mismatch",
        }
        reason = str(exc)
        return {"file_verified": False,
                "reason": reason if reason in safe else "source_model_unavailable"}


def audit(*, hostname: str | None = None, process_reader=process_binding,
          file_reader=verify_file) -> dict:
    host_ok = (hostname or socket.gethostname()).split(".", 1)[0].lower() == HOSTNAME
    bound, pid, process_reason = (
        process_reader() if host_ok else (False, None, "wrong_observer_host")
    )
    file_result = (
        file_reader() if host_ok and bound else
        {"file_verified": False, "reason": "source_binding_not_verified"}
    )
    return {
        "schema": SCHEMA,
        "read_only": True,
        "verdict": "HOLD",
        "expected_port": SOURCE_PORT,
        "observer_host_verified": host_ok,
        "canonical_process_verified": bound,
        "process_reason": process_reason,
        "canonical_process_pid": pid if bound else None,
        "disk_file_matches_guarded_canary_pinned_sha256": (
            file_result["file_verified"]
        ),
        "disk_file_reason": file_result["reason"],
        "guarded_process_weights_verified": False,
        "resident_loaded_weights_verified": False,
        "equivalent_request_behavior_verified": False,
        "source_model_requests_sent": False,
        "credential_values_read": False,
        "service_change_authorized": False,
        "router_cutover_authorized": False,
    }


def main() -> int:
    print(json.dumps(audit(), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
