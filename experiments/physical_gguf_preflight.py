#!/usr/bin/env python3
"""Host-local, read-only llama-server preflight. NOT an authority or approval tool.

Compare a running physical process's GGUF bytes to a separately operator-reviewed
expected SHA256 and size, and check a localhost-only model inventory. Never
uploads input, contacts a remote host, writes data, issues model completions,
edits Neo4j, renews approvals, or produces a dispatch-capable receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_MODEL_BYTES = 2 * 1024 * 1024 * 1024
MAX_PROCESS_ARGS_BYTES = 16_384
HASH = re.compile(r"[0-9a-f]{64}\Z")


def argument(argv: list[str], aliases: tuple[str, ...]) -> str | None:
    matches: list[str] = []
    for i, value in enumerate(argv):
        if value in aliases:
            if i + 1 >= len(argv):
                return None
            matches.append(argv[i + 1])
        for alias in aliases:
            if value.startswith(alias + "="):
                matches.append(value[len(alias) + 1:])
    return matches[0] if len(matches) == 1 else None


def process_candidates(proc_root: Path) -> list[dict[str, Any]]:
    rows = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            with (entry / "cmdline").open("rb") as stream:
                payload = stream.read(MAX_PROCESS_ARGS_BYTES + 1)
            if len(payload) > MAX_PROCESS_ARGS_BYTES:
                continue
            argv = [x.decode("utf-8", errors="replace")
                    for x in payload.split(b"\0") if x]
            if not argv or Path(argv[0]).name != "llama-server":
                continue
            rows.append({
                "pid": int(entry.name),
                "argv": argv,
                "cmdline_digest": hashlib.sha256(payload).hexdigest(),
            })
        except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
            continue
    return rows


def file_identity(path: Path, expected_bytes: int, expected_sha256: str) -> bool:
    """Hash a bounded regular file and check it was not replaced during reading."""
    if not path.is_absolute() or path.is_symlink():
        return False
    try:
        before = path.stat()
        if (not stat.S_ISREG(before.st_mode) or before.st_size != expected_bytes
                or before.st_size > MAX_MODEL_BYTES):
            return False
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        return (
            digest == expected_sha256 and
            (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns)
        )
    except (OSError, PermissionError, ValueError):
        return False


def local_inventory(url: str, expected_model: str) -> tuple[int, int]:
    """Observe localhost only. No authorization implied by a catalog."""
    # Avoid honoring remote URLs or injecting endpoint paths into this code.
    if url != "http://127.0.0.1:1234/api/v0/models":
        raise ValueError("only the local LM Studio inventory is allowed")
    with urllib.request.urlopen(url, timeout=3) as response:
        if response.status != 200:
            raise ValueError("LM Studio inventory returned non-200 status")
        raw = response.read(256_001)
    if len(raw) > 256_000:
        raise ValueError("inventory response exceeded size budget")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("invalid LM Studio model inventory shape")
    data = payload["data"]
    if len(data) > 256:
        raise ValueError("inventory contains too many models")
    loaded = [x for x in data if isinstance(x, dict)
              and (x.get("state") == "loaded" or bool(x.get("loaded_instances")))]
    count_expected = sum(
        x.get("id") == expected_model or x.get("model_key") == expected_model
        for x in loaded
    )
    return len(loaded), count_expected


def observe(
    *, expected_sha256: str, expected_bytes: int, expected_context: int,
    expected_model: str, proc_root: Path = Path("/proc"),
    inventory_reader: Any = local_inventory,
) -> dict[str, Any]:
    if not HASH.fullmatch(expected_sha256):
        raise ValueError("expected SHA256 must be exact lowercase hex digest")
    if type(expected_bytes) is not int or not 0 < expected_bytes <= MAX_MODEL_BYTES:
        raise ValueError("invalid expected model size")
    if type(expected_context) is not int or not 1 <= expected_context <= 1_048_576:
        raise ValueError("invalid expected context")
    if not isinstance(expected_model, str) or not (
        1 <= len(expected_model) <= 160 and
        re.fullmatch(r"[A-Za-z0-9_./-]+", expected_model)
    ):
        raise ValueError("invalid expected model identifier")

    candidates = process_candidates(proc_root)
    matching: list[dict] = []
    for row in candidates:
        argv = row["argv"]
        model = argument(argv, ("-m", "--model"))
        context = argument(argv, ("-c", "--ctx-size"))
        parallel = argument(argv, ("-np", "--parallel"))
        if model is None or context is None:
            continue
        try:
            ctx = int(context)
            instances = int(parallel) if parallel is not None else None
        except ValueError:
            continue
        if ctx != expected_context or instances is None or instances < 1:
            continue
        if not file_identity(Path(model), expected_bytes, expected_sha256):
            continue
        # Guard against PID reuse/restart during file hashing.
        try:
            with (proc_root / str(row["pid"]) / "cmdline").open("rb") as stream:
                current = stream.read(MAX_PROCESS_ARGS_BYTES + 1)
            if hashlib.sha256(current).hexdigest() != row["cmdline_digest"]:
                continue
        except (OSError, PermissionError):
            continue
        matching.append({"pid": row["pid"], "context": ctx, "parallel": instances})
    try:
        loaded_count, matching_inventory = inventory_reader(
            "http://127.0.0.1:1234/api/v0/models", expected_model,
        )
        if (type(loaded_count) is not int or type(matching_inventory) is not int
                or loaded_count < 0 or matching_inventory < 0):
            raise ValueError("invalid inventory counts")
        catalog_reachable = True
    except (ValueError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        loaded_count, matching_inventory = 0, 0
        catalog_reachable = False
        _ = exc
    acceptable = (len(matching) == 1 and catalog_reachable
                  and loaded_count == 1 and matching_inventory == 1)
    return {
        "schema": "physical-gguf-preflight-v1",
        "observed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "PHYSICAL_PRECHECK_PASSED" if acceptable else "BLOCKED",
        "physical_model_process_count": len(matching),
        "other_llama_server_processes": len(candidates) - len(matching),
        "matching_process_pid": matching[0]["pid"] if len(matching) == 1 else None,
        "configured_context": expected_context,
        "configured_parallel_slots": matching[0]["parallel"] if len(matching) == 1 else None,
        "expected_model_sha256": expected_sha256,
        "expected_model_bytes": expected_bytes,
        "local_inventory_accessible": catalog_reachable,
        "loaded_inventory_count": loaded_count,
        "matching_inventory_count": matching_inventory,
        "source": "unsigned_local_proc_and_inventory",
        "host_signed_execution_witness": False,
        "fresh_request_ownership_proven": False,
        "network_path_canaries_passed": False,
        "canonical_approval_created": False,
        "can_dispatch": False,
        "can_auto_renew": False,
        "operator_review_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-bytes", required=True, type=int)
    parser.add_argument("--expected-context", required=True, type=int)
    parser.add_argument("--expected-model", required=True)
    args = parser.parse_args()
    try:
        result = observe(
            expected_sha256=args.expected_sha256,
            expected_bytes=args.expected_bytes,
            expected_context=args.expected_context,
            expected_model=args.expected_model,
        )
    except (ValueError, OSError) as exc:
        print("physical preflight rejected: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == "PHYSICAL_PRECHECK_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
