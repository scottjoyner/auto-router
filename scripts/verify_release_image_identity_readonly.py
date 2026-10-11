#!/usr/bin/env python3
"""Read-only, fail-closed identity gate for supervised fleet router cutover.

Reads Compose service *structure* and Docker image IDs/revision labels only.
Never prints environment variables, does not build, pull, restart or deploy.
An image tag such as 'git-router' is NOT an immutable release candidate.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import yaml

IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST_REF = re.compile(r"^(?:[^\s@]+@)?sha256:[0-9a-f]{64}$")


def inspect_release_structure(
    compose: object, *,
    service: str, candidate_image_id: str, rollback_image_id: str,
    reviewed_source_sha: str, observed_running_image_id: str,
    observed_compose_image_id: str,
    candidate_embedded_revision: str, candidate_present: bool,
    rollback_present: bool,
) -> dict[str, object]:
    problems: list[str] = []
    services = compose.get("services") if isinstance(compose, dict) else None
    item = services.get(service) if isinstance(services, dict) else None
    if not isinstance(item, dict):
        problems.append("SERVICE_NOT_PRESENT")
        item = {}
    image_ref = item.get("image")
    if not isinstance(image_ref, str) or not DIGEST_REF.fullmatch(image_ref):
        problems.append("COMPOSE_IMAGE_NOT_IMMUTABLY_PINNED")
    if item.get("build") is not None:
        problems.append("COMPOSE_HAS_BUILD_STEP")
    if not IMAGE_ID.fullmatch(candidate_image_id):
        problems.append("CANDIDATE_IMAGE_ID_INVALID")
    # A pinned Compose digest and a valid, reviewed candidate image ID are
    # insufficient separately: the digest must resolve to this exact candidate.
    if observed_compose_image_id != candidate_image_id or not IMAGE_ID.fullmatch(observed_compose_image_id):
        problems.append("COMPOSE_DIGEST_DOES_NOT_RESOLVE_TO_CANDIDATE")
    if not IMAGE_ID.fullmatch(rollback_image_id):
        problems.append("ROLLBACK_IMAGE_ID_INVALID")
    if not SOURCE_SHA.fullmatch(reviewed_source_sha):
        problems.append("REVIEWED_SOURCE_SHA_INVALID")
    if candidate_image_id == rollback_image_id:
        problems.append("CANDIDATE_EQUALS_ROLLBACK")
    if observed_running_image_id != rollback_image_id:
        problems.append("RUNNING_IMAGE_DIFFERS_FROM_ROLLBACK_REFERENCE")
    if not candidate_present:
        problems.append("CANDIDATE_IMAGE_NOT_LOCALLY_AVAILABLE")
    if not rollback_present:
        problems.append("ROLLBACK_IMAGE_NOT_LOCALLY_AVAILABLE")
    if candidate_embedded_revision != reviewed_source_sha:
        problems.append("IMAGE_REVISION_LABEL_DOES_NOT_MATCH_REVIEWED_SOURCE")
    # Matching an OCI revision label is metadata-only, not a signature or
    # proof of an independently reproducible build.
    return {
        "schema": "router-supervised-image-identity-v1",
        "decision": "PASS_IDENTITY_PREFLIGHT_ONLY" if not problems else "HOLD_IMAGE_IDENTITY",
        "reason_codes": sorted(set(problems)),
        "file_or_secret_values_printed": False,
        "actual_deployment_performed": False,
        "credential_custody_verified": False,
        "router_auth_verified": False,
        "deployment_authorized": False,
    }


def docker_field(args: list[str]) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, timeout=8, check=False,
    )
    return result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", required=True)
    parser.add_argument("--service", default="llm-router")
    parser.add_argument("--container", default="git-router-1")
    parser.add_argument("--candidate-image-id", required=True)
    parser.add_argument("--rollback-image-id", required=True)
    parser.add_argument("--reviewed-source-sha", required=True)
    args = parser.parse_args()
    try:
        # Only a fixed set of safe image/container metadata fields is read.
        manifest = yaml.safe_load(Path(args.compose).read_text(encoding="utf-8"))
        actual = docker_field(["inspect", "--type", "container", "--format", "{{.Image}}", args.container])
        label = docker_field([
            "image", "inspect", "--format",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            args.candidate_image_id,
        ])
        candidate_id = docker_field([
            "image", "inspect", "--format", "{{.Id}}", args.candidate_image_id
        ])
        rollback_id = docker_field([
            "image", "inspect", "--format", "{{.Id}}", args.rollback_image_id
        ])
        services = manifest.get("services") if isinstance(manifest, dict) else None
        service_item = services.get(args.service) if isinstance(services, dict) else None
        compose_ref = service_item.get("image") if isinstance(service_item, dict) else None
        # Reject malformed/option-looking input before passing a YAML value to Docker.
        compose_image_id = docker_field([
            "image", "inspect", "--format", "{{.Id}}", compose_ref
        ]) if isinstance(compose_ref, str) and DIGEST_REF.fullmatch(compose_ref) else ""
        report = inspect_release_structure(
            manifest, service=args.service,
            candidate_image_id=args.candidate_image_id,
            rollback_image_id=args.rollback_image_id,
            reviewed_source_sha=args.reviewed_source_sha,
            observed_running_image_id=actual,
            observed_compose_image_id=compose_image_id,
            candidate_embedded_revision=label,
            candidate_present=candidate_id == args.candidate_image_id,
            rollback_present=rollback_id == args.rollback_image_id,
        )
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, yaml.YAMLError):
        report = {
            "schema": "router-supervised-image-identity-v1",
            "decision": "HOLD_IMAGE_METADATA_UNAVAILABLE",
            "reason_codes": ["INPUT_OR_RUNTIME_METADATA_ERROR"],
            "file_or_secret_values_printed": False,
            "deployment_authorized": False,
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["decision"] == "PASS_IDENTITY_PREFLIGHT_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
