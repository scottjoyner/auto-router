#!/usr/bin/env python3
"""Verify approved LAN and Tailscale paths from the shadow router container.

This is a read-only evidence collector. It does not alter routes, disconnect a path,
load a model, or admit a runtime. Failover selection must be tested separately by
running the shadow router with an intentionally unreachable LAN candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _docker_exec(container: str, code: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": "container probe did not return JSON",
        }
    return payload if isinstance(payload, dict) else {"ok": False, "payload": payload}


def _probe_code() -> str:
    return r'''
import json
import os
import urllib.error
import urllib.request

result = {"ok": True, "paths": []}
for name, transport in (
    ("RECONCILIATION_LAN_BASE_URL", "lan"),
    ("RECONCILIATION_TAILSCALE_BASE_URL", "tailscale"),
):
    base = os.getenv(name, "").strip().rstrip("/")
    item = {"environment": name, "transport": transport, "base_url": base}
    if not base:
        item.update({"ok": False, "error": "not configured"})
        result["ok"] = False
        result["paths"].append(item)
        continue
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=4) as response:
            item.update({"ok": response.status < 500, "status_code": response.status})
    except urllib.error.HTTPError as exc:
        item.update({"ok": exc.code < 500, "status_code": exc.code})
    except Exception as exc:
        item.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    if not item["ok"]:
        result["ok"] = False
    result["paths"].append(item)
print(json.dumps(result, sort_keys=True))
'''


def _fetch_admission(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"X-Admin-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "admission endpoint did not return an object"}
    payload["ok"] = True
    return payload


def _validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    container_probe = payload.get("container_probe")
    if not isinstance(container_probe, dict) or container_probe.get("ok") is not True:
        errors.append("container could not reach every configured LAN/Tailscale path")
    admission = payload.get("admission")
    if not isinstance(admission, dict) or admission.get("ok") is not True:
        errors.append("authenticated /admin/admission request failed")
        return errors
    runtimes = admission.get("runtimes")
    paths = admission.get("access_paths")
    if not isinstance(runtimes, list) or not runtimes:
        errors.append("admission endpoint contains no runtime capacity record")
    if not isinstance(paths, list) or not paths:
        errors.append("admission endpoint contains no access-path record")
    if isinstance(paths, list):
        for record in paths:
            if not isinstance(record, dict):
                continue
            approved = record.get("approved_access_urls")
            if not isinstance(approved, list) or len(approved) < 2:
                errors.append("runtime does not expose multiple approved access paths")
            selected = record.get("selected_access_url")
            if selected and selected not in approved:
                errors.append("selected access URL is not in the approved path list")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="auto-router-reconciliation")
    parser.add_argument(
        "--admission-url",
        default="http://127.0.0.1:18088/admin/admission",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts-reconciliation/network-path-evidence.json"),
    )
    args = parser.parse_args()

    token = os.getenv("AUTO_ROUTER_ADMIN_TOKEN", "").strip()
    if not token:
        print("BLOCKED: set AUTO_ROUTER_ADMIN_TOKEN", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "container": args.container,
        "admission_url": args.admission_url,
        "container_probe": _docker_exec(args.container, _probe_code()),
        "admission": _fetch_admission(args.admission_url, token),
        "mutation_performed": False,
    }
    errors = _validate(payload)
    payload["ok"] = not errors
    payload["errors"] = errors

    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    checksum = args.output.with_suffix(args.output.suffix + ".sha256")
    checksum.write_text(f"{digest}  {args.output.name}\n", encoding="utf-8")

    print(args.output)
    print(checksum)
    if errors:
        print("BLOCKED: reconciliation network path verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS: router container reaches approved LAN and Tailscale paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
