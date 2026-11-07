#!/usr/bin/env python3
"""Read-only custody check for an offline Docker image archive.

This verifies local archive identity, not GitHub's cryptographic provenance.
Before promotion, separately verify the exact archive with gh attestation verify
and require an independently pinned source SHA, signer workflow and artifact
SHA-256. This script NEVER loads images, deploys, touches Docker, or grants GO.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import re
import tarfile

REPO = "scottjoyner/auto-router"
SCHEMA = "router-offline-image-v1"
SHA1 = re.compile(r"[a-f0-9]{40}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")


class ReleaseHold(ValueError):
    """Artifact is not acceptable; fail closed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_member(archive: tarfile.TarFile, name: str, max_bytes: int) -> object:
    try:
        item = archive.getmember(name)
    except KeyError as exc:
        raise ReleaseHold("required Docker archive JSON entry absent") from exc
    if not item.isfile() or item.size < 1 or item.size > max_bytes:
        raise ReleaseHold("invalid Docker archive JSON entry size/type")
    stream = archive.extractfile(item)
    if stream is None:
        raise ReleaseHold("Docker archive JSON entry unreadable")
    try:
        return json.loads(stream.read(max_bytes + 1).decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ReleaseHold("invalid Docker archive JSON") from exc


def _archive_identity(path: Path, source_sha: str) -> str:
    if not SHA1.fullmatch(source_sha):
        raise ReleaseHold("source SHA must be an exact lowercase 40-character commit")
    expected_tag = f"local/router-release:{source_sha}"
    try:
        with tarfile.open(path, mode="r:") as archive:
            manifest = _json_member(archive, "manifest.json", 512 * 1024)
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise ReleaseHold("expected a single-image Docker archive")
            record = manifest[0]
            if not isinstance(record, dict) or record.get("RepoTags") != [expected_tag]:
                raise ReleaseHold("archive image tag does not match exact source SHA")
            config_name = record.get("Config")
            if (not isinstance(config_name, str)
                    or not re.fullmatch(r"[a-f0-9]{64}\.json", config_name)):
                raise ReleaseHold("invalid Docker archive config filename")
            image = _json_member(archive, config_name, 8 * 1024 * 1024)
            if not isinstance(image, dict):
                raise ReleaseHold("invalid image configuration")
            config = image.get("config", image.get("Config"))
            labels = config.get("Labels") if isinstance(config, dict) else None
            if not isinstance(labels, dict) or labels.get(
                "org.opencontainers.image.revision"
            ) != source_sha:
                raise ReleaseHold("embedded image revision is not the pinned source SHA")
            # Docker image ID identifies the SHA-256 of config JSON. Do not
            # trust an arbitrary producer-supplied image ID from the manifest.
            return "sha256:" + hashlib.sha256(
                archive.extractfile(archive.getmember(config_name)).read()
            ).hexdigest()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseHold("Docker archive unreadable or malformed") from exc


def create(archive: Path, output: Path, source_sha: str) -> dict:
    image_id = _archive_identity(archive, source_sha)
    data = {
        "schema": SCHEMA,
        "repository": REPO,
        "source_sha": source_sha,
        "archive_name": archive.name,
        "archive_sha256": _sha256(archive),
        "archive_bytes": archive.stat().st_size,
        "image_id": image_id,
        "image_tag": f"local/router-release:{source_sha}",
        "attestation_verified": False,
        "release_decision": "HOLD",
    }
    if output.exists():
        raise ReleaseHold("refusing to overwrite a release manifest")
    output.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return data


def verify(archive: Path, manifest_file: Path, pinned_source: str,
           pinned_archive_sha256: str) -> dict:
    if not SHA1.fullmatch(pinned_source) or not SHA256.fullmatch(pinned_archive_sha256):
        raise ReleaseHold("expected source and archive digests must be externally pinned")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ReleaseHold("invalid manifest")
    if any(manifest.get(key) != value for key, value in {
        "schema": SCHEMA, "repository": REPO, "source_sha": pinned_source,
        "archive_name": archive.name, "image_tag": f"local/router-release:{pinned_source}",
        "attestation_verified": False, "release_decision": "HOLD",
    }.items()):
        raise ReleaseHold("manifest identity mismatch")
    # Check the externally pinned digest BEFORE self-reported size. An attacker
    # appending bytes must yield a clear cryptographic integrity failure, not
    # a misleading benign-looking manifest metadata mismatch.
    digest = _sha256(archive)
    if not (hmac.compare_digest(digest, pinned_archive_sha256)
            and hmac.compare_digest(digest, str(manifest.get("archive_sha256")))):
        raise ReleaseHold("artifact SHA-256 mismatch")
    if manifest.get("archive_bytes") != archive.stat().st_size:
        raise ReleaseHold("manifest archive size mismatch")
    image_id = _archive_identity(archive, pinned_source)
    if not IMAGE_ID.fullmatch(image_id) or image_id != manifest.get("image_id"):
        raise ReleaseHold("Docker archive embedded image ID mismatch")
    return {
        "schema": SCHEMA,
        "source_sha": pinned_source,
        "archive_sha256": digest,
        "image_id": image_id,
        "integrity_result": "PASS",
        "provenance_result": "NOT_CHECKED",
        "release_decision": "HOLD",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create_cmd = sub.add_parser("create")
    create_cmd.add_argument("--archive", type=Path, required=True)
    create_cmd.add_argument("--source-sha", required=True)
    create_cmd.add_argument("--output", type=Path, required=True)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--archive", type=Path, required=True)
    verify_cmd.add_argument("--manifest", type=Path, required=True)
    verify_cmd.add_argument("--expected-source-sha", required=True)
    verify_cmd.add_argument("--expected-archive-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create(args.archive, args.output, args.source_sha)
        else:
            result = verify(
                args.archive, args.manifest,
                args.expected_source_sha, args.expected_archive_sha256,
            )
    except (ReleaseHold, OSError, ValueError) as exc:
        parser.exit(1, f"HOLD: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
