#!/usr/bin/env python3
"""Status-only inspection of the *actual* operator Compose file stack.

Never runs up, pull, build, or restart. Compose output (which may contain
private interpolation values) is processed in memory and never logged or saved.
This preflight is necessary but insufficient for supervised deployment.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_CONFIG_BYTES = 4 * 1024 * 1024


def assess_merged_service(config: object, *, service: str, expected_image_id: str) -> dict[str, object]:
    problems: list[str] = []
    services = config.get("services") if isinstance(config, dict) else None
    entry = services.get(service) if isinstance(services, dict) else None
    if not isinstance(entry, dict):
        problems.append("ROUTER_SERVICE_MISSING")
        entry = {}
    if not IMAGE_ID.fullmatch(expected_image_id):
        problems.append("EXPECTED_CANDIDATE_ID_INVALID")
    if entry.get("image") != expected_image_id:
        problems.append("COMPOSE_IMAGE_NOT_EXACT_CANDIDATE_ID")
    if entry.get("build") is not None:
        problems.append("COMPOSE_BUILD_STILL_PRESENT")
    if entry.get("pull_policy") != "never":
        problems.append("COMPOSE_PULL_POLICY_NOT_NEVER")
    return {
        "schema": "auto-router.private-compose-image-preflight-v1",
        "decision": "PASS_PRIVATE_COMPOSE_STRUCTURE_ONLY" if not problems else "HOLD_PRIVATE_COMPOSE",
        "reason_codes": sorted(set(problems)),
        "compose_file_contents_disclosed": False,
        "environment_values_disclosed": False,
        "model_residency_verified": False,
        "remote_admin_auth_verified": False,
        "credential_custody_verified": False,
        "production_release_authorized": False,
        "image_staged_or_deployed": False,
    }


def _hold(reason: str) -> dict[str, object]:
    report = assess_merged_service({}, service="llm-router", expected_image_id="sha256:" + "0" * 64)
    report["reason_codes"] = [reason]
    return report


def run_private_preflight(compose_files: list[str], *, service: str, expected_image_id: str) -> dict[str, object]:
    if (not compose_files or len(compose_files) > 12
            or any(not Path(x).is_file() for x in compose_files)
            or not IMAGE_ID.fullmatch(expected_image_id)):
        return _hold("INVALID_INPUT_OR_COMPOSE_FILE")
    argv = ["docker", "compose"]
    for filename in compose_files:
        argv.extend(["-f", filename])
    # Avoid resolving env_file contents. Compose interpolation can still carry
    # private values: stdout and stderr must stay in process memory.
    argv.extend(["config", "--no-env-resolution", "--format", "json"])
    try:
        result = subprocess.run(argv, capture_output=True, timeout=25, check=False)
        if result.returncode != 0 or len(result.stdout) > _MAX_CONFIG_BYTES:
            return _hold("PRIVATE_COMPOSE_CONFIG_UNAVAILABLE")
        data = json.loads(result.stdout)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return _hold("PRIVATE_COMPOSE_CONFIG_UNAVAILABLE")
    return assess_merged_service(data, service=service, expected_image_id=expected_image_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", action="append", required=True,
                        help="Actual operator Compose files in exact deployment order")
    parser.add_argument("--service", default="llm-router")
    parser.add_argument("--expected-image-id", required=True)
    args = parser.parse_args()
    report = run_private_preflight(
        args.compose_file, service=args.service, expected_image_id=args.expected_image_id
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["decision"] == "PASS_PRIVATE_COMPOSE_STRUCTURE_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
