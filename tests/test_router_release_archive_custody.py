"""No-network release archive custody regression tests.

Synthetic tar data only. Nothing calls Docker or claims cryptographic
signer validation; an externally pinned hash and Sigstore check are separate.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from tools.router_release_archive import ReleaseHold, create, verify

GOOD_SHA = "a" * 40
OTHER_SHA = "b" * 40


def _add(tar: tarfile.TarFile, name: str, content: bytes) -> None:
    item = tarfile.TarInfo(name)
    item.size = len(content)
    tar.addfile(item, io.BytesIO(content))


def _fixture_archive(tmp_path: Path, *, revision: str = GOOD_SHA,
                     tag_sha: str = GOOD_SHA, raw_route: bool = False) -> Path:
    config = {
        "architecture": "amd64",
        "config": {
            "Labels": {"org.opencontainers.image.revision": revision},
        },
    }
    blob = json.dumps(config, sort_keys=True).encode()
    config_name = hashlib.sha256(blob).hexdigest() + ".json"
    docker_manifest = [{
        "Config": config_name,
        "RepoTags": [f"local/router-release:{tag_sha}"],
        "Layers": [],
    }]
    if raw_route:
        docker_manifest[0]["RepoTags"] = ["local/router:latest"]
    file_path = tmp_path / "candidate.docker.tar"
    with tarfile.open(file_path, mode="w") as tar:
        _add(tar, config_name, blob)
        _add(tar, "manifest.json", json.dumps(docker_manifest).encode())
    return file_path


def _make(tmp_path: Path) -> tuple[Path, Path, dict]:
    archive = _fixture_archive(tmp_path)
    manifest_file = tmp_path / "release-manifest.json"
    manifest = create(archive, manifest_file, GOOD_SHA)
    return archive, manifest_file, manifest


def test_valid_synthetic_archive_is_integrity_only_and_never_go(tmp_path):
    archive, manifest_file, manifest = _make(tmp_path)
    assert manifest["release_decision"] == "HOLD"
    assert manifest["attestation_verified"] is False
    result = verify(archive, manifest_file, GOOD_SHA, manifest["archive_sha256"])
    assert result["integrity_result"] == "PASS"
    assert result["provenance_result"] == "NOT_CHECKED"
    assert result["release_decision"] == "HOLD"


def test_attacker_supplied_source_identity_cannot_replace_pinned_sha(tmp_path):
    archive, manifest_file, manifest = _make(tmp_path)
    with pytest.raises(ReleaseHold, match="manifest identity mismatch"):
        verify(archive, manifest_file, OTHER_SHA, manifest["archive_sha256"])


def test_attacker_supplied_archive_hash_cannot_replace_external_digest(tmp_path):
    archive, manifest_file, _ = _make(tmp_path)
    with pytest.raises(ReleaseHold, match="artifact SHA-256 mismatch"):
        verify(archive, manifest_file, GOOD_SHA, "0" * 64)


def test_tampered_tar_after_manifest_creation_is_rejected(tmp_path):
    archive, manifest_file, manifest = _make(tmp_path)
    with archive.open("ab") as file:
        file.write(b"TAMPERING")
    with pytest.raises(ReleaseHold, match="artifact SHA-256 mismatch"):
        verify(archive, manifest_file, GOOD_SHA, manifest["archive_sha256"])


def test_tampered_metadata_or_fake_release_go_is_rejected(tmp_path):
    archive, manifest_file, manifest = _make(tmp_path)
    manifest["release_decision"] = "GO"
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(ReleaseHold, match="manifest identity mismatch"):
        verify(archive, manifest_file, GOOD_SHA, manifest["archive_sha256"])


def test_mismatched_embedded_source_revision_fails_closed(tmp_path):
    archive = _fixture_archive(tmp_path, revision=OTHER_SHA)
    with pytest.raises(ReleaseHold, match="embedded image revision"):
        create(archive, tmp_path / "manifest.json", GOOD_SHA)


def test_wrong_image_tag_fails_closed(tmp_path):
    archive = _fixture_archive(tmp_path, raw_route=True)
    with pytest.raises(ReleaseHold, match="archive image tag"):
        create(archive, tmp_path / "manifest.json", GOOD_SHA)


def test_source_sha_cannot_be_shortened_or_forged(tmp_path):
    archive = _fixture_archive(tmp_path)
    with pytest.raises(ReleaseHold, match="source SHA"):
        create(archive, tmp_path / "manifest.json", "a" * 7)


def test_manifest_does_not_overwrite_existing_file(tmp_path):
    archive = _fixture_archive(tmp_path)
    target = tmp_path / "manifest.json"
    target.write_text("preserve preexisting file")
    with pytest.raises(ReleaseHold, match="refusing to overwrite"):
        create(archive, target, GOOD_SHA)
    assert target.read_text() == "preserve preexisting file"


def test_missing_docker_manifest_does_not_create_release_manifest(tmp_path):
    archive = tmp_path / "candidate.docker.tar"
    with tarfile.open(archive, mode="w") as tar:
        _add(tar, "irrelevant.json", b"{}")
    with pytest.raises(ReleaseHold, match="absent"):
        create(archive, tmp_path / "manifest.json", GOOD_SHA)
    assert not (tmp_path / "manifest.json").exists()
