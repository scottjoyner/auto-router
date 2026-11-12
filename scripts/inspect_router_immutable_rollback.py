#!/usr/bin/env python3
"""Read-only Router image/Compose rollback metadata and immutable identity gate.

Only Docker inspect and trusted existing Compose parsing are permitted.
A cached old image or intact mutable tag NEVER proves rollback restoration.
No network calls, image save/load/tag, Docker mutation or credential output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

import yaml

if __package__:
    from .inspect_router_cutover_topology import evaluate as topology_evaluate
else:
    from inspect_router_cutover_topology import evaluate as topology_evaluate

ROUTER = "git-router-1"
MUTABLE_TAG = "git-router:latest"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


class RollbackPreflightDenied(RuntimeError):
    pass


def docker_read(*argv: str, optional: bool = False) -> dict | None:
    admissible = (
        argv == ("inspect", ROUTER) or
        len(argv) == 3 and argv[:2] == ("image", "inspect")
        and (argv[2] == MUTABLE_TAG or bool(IMAGE_ID.fullmatch(argv[2])))
    )
    if not admissible:
        raise RollbackPreflightDenied("unapproved Docker read")
    try:
        result = subprocess.run(
            ["docker", *argv], capture_output=True, text=True,
            check=False, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RollbackPreflightDenied("Docker inspection unavailable") from None
    if result.returncode:
        if optional:
            return None
        raise RollbackPreflightDenied("Docker inspect failed")
    if len(result.stdout) > 2_000_000:
        raise RollbackPreflightDenied("Docker metadata too large")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise RollbackPreflightDenied("unparseable Docker metadata") from None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RollbackPreflightDenied("malformed Docker metadata")
    return value[0]


def read_compose(compose_path: Path, router: dict) -> dict:
    label = ((router.get("Config") or {}).get("Labels") or {}).get(
        "com.docker.compose.project.config_files")
    if not isinstance(label, str) or not label:
        raise RollbackPreflightDenied("Compose source not bound to running container")
    if compose_path.is_symlink() or not compose_path.is_file():
        raise RollbackPreflightDenied("Compose source not a regular direct file")
    if compose_path.resolve() != Path(label).resolve():
        raise RollbackPreflightDenied("Compose source does not match running container")
    try:
        raw = compose_path.read_bytes()
        if not raw or len(raw) > 200_000:
            raise RollbackPreflightDenied("Compose source missing or excessive")
        value = yaml.safe_load(raw)
    except (OSError, ValueError, yaml.YAMLError):
        raise RollbackPreflightDenied("Compose inspection failed") from None
    if not isinstance(value, dict):
        raise RollbackPreflightDenied("Compose configuration invalid")
    return value


def evaluate(
    compose: dict, router: dict, previous: dict | None,
    mutable_tag: dict | None, candidate: dict | None,
    expected_previous: str, expected_candidate: str,
) -> dict:
    if not IMAGE_ID.fullmatch(expected_previous) or not IMAGE_ID.fullmatch(expected_candidate):
        raise RollbackPreflightDenied("immutable image IDs required")
    if expected_previous == expected_candidate:
        raise RollbackPreflightDenied("candidate must differ from prior release")
    if router.get("Name") != "/" + ROUTER:
        raise RollbackPreflightDenied("unexpected live Router identity")
    actual_previous = router.get("Image")
    previous_cached = previous is not None and previous.get("Id") == expected_previous
    candidate_cached = candidate is not None and candidate.get("Id") == expected_candidate
    tag_matches = mutable_tag is not None and mutable_tag.get("Id") == expected_previous
    topology = topology_evaluate(compose, router, previous_cached)
    problems = []
    if actual_previous != expected_previous:
        problems.append("RUNNING_IMAGE_CHANGED")
    if not previous_cached:
        problems.append("PREVIOUS_IMAGE_NOT_CACHED")
    if not candidate_cached:
        problems.append("CANDIDATE_IMAGE_NOT_CACHED_ON_THIS_HOST")
    if not tag_matches:
        problems.append("MUTABLE_COMPOSE_TAG_DRIFT")
    if not topology["compose_compatibility_pass"]:
        problems.append("COMPOSE_OR_LIVE_TOPOLOGY_DRIFT")
    if (router.get("State") or {}).get("Status") != "running":
        problems.append("ROUTER_NOT_RUNNING")
    if (router.get("HostConfig") or {}).get("RestartPolicy", {}).get("Name") != "unless-stopped":
        problems.append("RESTART_POLICY_DRIFT")

    return {
        "schema": 1,
        "verdict": "ROLLBACK_METADATA_PRECHECK_HOLD" if not problems
            else "ROLLBACK_PRECONDITIONS_DRIFT_HOLD",
        "blocker_codes": sorted(problems),
        "running_image_matches_pinned_previous": actual_previous == expected_previous,
        "previous_image_cached_by_immutable_id": previous_cached,
        "candidate_image_cached_by_immutable_id": candidate_cached,
        "mutable_compose_tag_matches_previous_now": tag_matches,
        "compose_uses_mutable_tag": True,
        "compose_and_mount_contract_matches": topology["compose_compatibility_pass"],
        "read_only": True,
        "secrets_or_mount_sources_emitted": False,
        "tag_is_immutable_rollback_reference": False,
        "previous_image_export_hash_independently_verified": False,
        "original_config_custody_independently_verified": False,
        "restoration_on_disposable_node_rehearsed": False,
        "owner_authorized_cutover": False,
        "post_deployment_inference_and_auth_acceptance": False,
        "production_rollback_proven": False,
        "production_cutover_allowed": False,
        "production_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only immutable Router rollback inventory")
    parser.add_argument("--compose-file", required=True, type=Path)
    parser.add_argument("--expected-running-image", required=True)
    parser.add_argument("--expected-candidate-image", required=True)
    args = parser.parse_args()
    try:
        if not IMAGE_ID.fullmatch(args.expected_running_image) or not IMAGE_ID.fullmatch(
            args.expected_candidate_image
        ):
            raise RollbackPreflightDenied("invalid immutable image pin")
        live = docker_read("inspect", ROUTER)
        compose = read_compose(args.compose_file, live)
        previous = docker_read("image", "inspect", args.expected_running_image, optional=True)
        tag = docker_read("image", "inspect", MUTABLE_TAG, optional=True)
        candidate = docker_read("image", "inspect", args.expected_candidate_image, optional=True)
        report = evaluate(compose, live, previous, tag, candidate,
                          args.expected_running_image, args.expected_candidate_image)
    except RollbackPreflightDenied:
        print(json.dumps({
            "schema": 1, "verdict": "ROLLBACK_INSPECTION_UNAVAILABLE_HOLD",
            "production_rollback_proven": False,
            "production_cutover_allowed": False,
            "production_modified": False,
            "secrets_or_mount_sources_emitted": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    # This inventory must never become a CI/deployment GO signal. A pristine
    # metadata snapshot cannot attest to an independently rehearsed rollback.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
