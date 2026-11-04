#!/usr/bin/env python3
"""Status-only inspection of the *actual* operator Compose file stack.

Never runs up, pull, build, or restart. Compose output (which may contain
private interpolation values) is processed in memory and never logged or saved.
This preflight is necessary but insufficient for supervised deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import time
from copy import deepcopy
from pathlib import Path

IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_SYNTHETIC_OVERLAY_BYTES = 4 * 1024
_COMPOSE_CONFIG_TIMEOUT = 25.0


def _bounded_compose_config(argv: list[str], *, input: bytes | None = None) -> bytes | None:
    """Read Docker Compose config with a real byte cap, deadline, no logging.

    Compose may resolve private interpolation even with --no-env-resolution.
    Never echo its stdout, stderr, argv, or a child exception to a caller.
    This starts only docker compose config, never up/pull/build/restart.
    """
    if input is not None and (
        type(input) is not bytes or len(input) > _MAX_SYNTHETIC_OVERLAY_BYTES
    ):
        return None
    proc = None
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            close_fds=True, start_new_session=True,
        )
        deadline = time.monotonic() + _COMPOSE_CONFIG_TIMEOUT
        if input is not None:
            assert proc.stdin is not None
            # The synthetic overlay is smaller than PIPE_BUF and contains no
            # private Compose values. Write it once and close stdin promptly.
            proc.stdin.write(input)
            proc.stdin.close()
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            assert proc.stdout is not None
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                ready = selector.select(remaining)
                if not ready:
                    return None
                for key, _ in ready:
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if len(output) + len(data) > _MAX_CONFIG_BYTES:
                        return None
                    output.extend(data)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or proc.wait(timeout=remaining) != 0:
            return None
        return bytes(output)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    finally:
        if proc is not None:
            # A timed-out child or descendant holding the output pipe open
            # must be killed and reaped. Keep stderr private in all cases.
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            if proc.stdin is not None:
                proc.stdin.close()
            if proc.stdout is not None:
                proc.stdout.close()


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
        result = _bounded_compose_config(argv)
        if result is None:
            return _hold("PRIVATE_COMPOSE_CONFIG_UNAVAILABLE")
        data = json.loads(result)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return _hold("PRIVATE_COMPOSE_CONFIG_UNAVAILABLE")
    return assess_merged_service(data, service=service, expected_image_id=expected_image_id)



def run_synthetic_overlay_check(compose_files: list[str], *, service: str) -> dict[str, object]:
    """Compare private Compose before/after an in-memory, never-deployed override."""
    if (not compose_files or len(compose_files) > 12
            or any(not Path(x).is_file() for x in compose_files)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", service)):
        return _hold("INVALID_INPUT_OR_COMPOSE_FILE")

    base = ["docker", "compose"]
    for filename in compose_files:
        base.extend(["-f", filename])
    dummy_id = "sha256:" + "c" * 64
    # The synthetic ID is never loaded or made a candidate for deployment.
    payload = (
        "services:\n  " + service + ":\n    image: " + dummy_id
        + "\n    pull_policy: never\n"
    ).encode()
    try:
        original = _bounded_compose_config(
            base + ["config", "--no-env-resolution", "--format", "json"],
        )
        candidate = _bounded_compose_config(
            base + ["-f", "-", "config", "--no-env-resolution", "--format", "json"],
            input=payload,
        )
        if original is None or candidate is None:
            return _hold("PRIVATE_COMPOSE_CONFIG_UNAVAILABLE")
        before, after = json.loads(original), json.loads(candidate)
        baseline = before["services"][service]
        updated = after["services"][service]
        if not isinstance(baseline, dict) or not isinstance(updated, dict):
            return _hold("PRIVATE_COMPOSE_SCHEMA_INVALID")
        # Private interpolation values remain in memory. Only boolean checks
        # appear in the report. Ignore precisely two intended override fields.
        original_other = {k: v for k, v in baseline.items() if k not in {"image", "pull_policy"}}
        updated_other = {k: v for k, v in updated.items() if k not in {"image", "pull_policy"}}
        others_before = {k: v for k, v in before["services"].items() if k != service}
        others_after = {k: v for k, v in after["services"].items() if k != service}
        # Compare the whole resolved document as well: changes to project name,
        # top-level secrets/configs or extensions must not slip through narrower
        # per-service checks. Keep all actual values private in memory.
        original_full = deepcopy(before)
        updated_full = deepcopy(after)
        for obj in (original_full, updated_full):
            obj["services"][service].pop("image", None)
            obj["services"][service].pop("pull_policy", None)
        tests = {
            "all_other_compose_fields_unchanged": original_full == updated_full,
            "synthetic_image_selected": updated.get("image") == dummy_id,
            "no_pull_requested": updated.get("pull_policy") == "never",
            "no_build_in_resolved_service": updated.get("build") is None,
            "router_other_fields_unchanged": original_other == updated_other,
            "other_services_unchanged": others_before == others_after,
            "network_config_unchanged": before.get("networks") == after.get("networks"),
            "volume_config_unchanged": before.get("volumes") == after.get("volumes"),
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return _hold("PRIVATE_COMPOSE_CONFIG_UNAVAILABLE")
    report = _hold("SYNTHETIC_OVERLAY_INVARIANT_FAILED")
    report["decision"] = "PASS_SYNTHETIC_OVERLAY_STRUCTURE_ONLY" if all(tests.values()) else "HOLD_PRIVATE_COMPOSE"
    report["reason_codes"] = [] if all(tests.values()) else [key.upper() for key, ok in tests.items() if not ok]
    report["checks"] = tests
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", action="append", required=True,
                        help="Actual operator Compose files in exact deployment order")
    parser.add_argument("--service", default="llm-router")
    parser.add_argument("--expected-image-id")
    parser.add_argument("--synthetic-overlay", action="store_true",
                        help="Check in-memory image override against the real Compose stack")
    args = parser.parse_args()
    report = (run_synthetic_overlay_check(args.compose_file, service=args.service)
              if args.synthetic_overlay else
              run_private_preflight(args.compose_file, service=args.service,
                                   expected_image_id=args.expected_image_id or ""))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["decision"] in ("PASS_PRIVATE_COMPOSE_STRUCTURE_ONLY",
                                        "PASS_SYNTHETIC_OVERLAY_STRUCTURE_ONLY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
