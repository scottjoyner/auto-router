"""Synthetic Ed25519 v2 custody: root, EFI and whole disk in one signature.

No live backup, TPM, host disk, reboot or operator private key involved.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from scripts.verify_x1_offline_recovery_bundle_v2 import (
    SCHEMA, verify_bundle, main,
)

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "fixture_gpt_disk", _ROOT / "tests" / "test_x1_offline_gpt_mirror.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_FIXTURES = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURES)


def _canonical(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _case(directory: Path):
    media = directory / "detached-media"
    media.mkdir()
    root, efi, disk = (
        media / "root.img", media / "efi.img", media / "whole-disk.raw",
    )
    root.write_bytes(b"synthetic-root" * 1000)
    efi.write_bytes(b"synthetic-efi" * 500)
    disk.write_bytes(_FIXTURES.disk_fixture())
    metadata = {
        "schema": SCHEMA,
        "host_id": "x1-370",
        "backup_set_id": "synthetic-bundle-id-0001",
        "generation": 42,
    }
    for label, path in (("root", root), ("efi", efi), ("disk", disk)):
        data = path.read_bytes()
        metadata[label + "_sha256"] = hashlib.sha256(data).hexdigest()
        metadata[label + "_bytes"] = len(data)
    key = Ed25519PrivateKey.generate()
    trusted = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    manifest = media / "signed.json"
    manifest.write_bytes(_canonical(metadata))
    return media, manifest, root, efi, disk, metadata, key, trusted


def _verify(fixture, *, manifest_data=None, high_water=42,
            signature_key=None, public_key=None, **overrides):
    media, manifest, root, efi, disk, metadata, private, key = fixture
    if manifest_data is not None:
        manifest.write_bytes(manifest_data)
    sig = (signature_key or private).sign(manifest.read_bytes()).hex()
    opts = dict(
        media_root=media, manifest=manifest, root_image=root,
        efi_image=efi, disk_image=disk, signature_hex=sig,
        trusted_public_key_hex=public_key or key,
        high_water_generation=high_water,
    )
    return verify_bundle(**{**opts, **overrides})


def test_real_three_file_signed_bundle_matches_but_restore_remains_hold():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _case(Path(tmp))
        report = _verify(fixture)
        assert report["single_manifest_signature_verified_for_supplied_key"]
        assert report["generation_floor_satisfied"]
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert report["whole_disk_primary_backup_gpt_consistent"]
        assert report["observation_complete"]
        assert not report["captured_same_source_snapshot_independently_proven"]
        assert not report["independent_signer_key_custody_verified"]
        assert not report["physical_clone_restore_and_uefi_boot_witnessed"]
        assert not report["production_repair_or_reboot_permitted"]
        assert report["decision"] == "HOLD"
        assert str(fixture[0]) not in json.dumps(report)


@pytest.mark.parametrize("index", [2, 3, 4])
def test_mutation_of_any_signed_file_fails_whole_bundle(index):
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _case(Path(tmp))
        target = fixture[index]
        with target.open("ab") as handle:
            handle.write(b"changed")
        report = _verify(fixture)
        assert report["single_manifest_signature_verified_for_supplied_key"]
        assert not report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert not report["whole_disk_primary_backup_gpt_consistent"]
        assert report["decision"] == "HOLD"


def test_signed_but_structurally_damaged_disk_cannot_claim_restore():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _case(Path(tmp))
        media, manifest, root, efi, disk, metadata, key, trusted = fixture
        damaged = bytearray(disk.read_bytes())
        damaged[512] ^= 0xFF
        disk.write_bytes(damaged)
        metadata = {**metadata,
                    "disk_sha256": hashlib.sha256(damaged).hexdigest()}
        manifest.write_bytes(_canonical(metadata))
        report = _verify(fixture)
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert not report["whole_disk_primary_backup_gpt_consistent"]
        assert report["decision"] == "HOLD"


def test_wrong_signer_and_wrong_external_generation_floor_fail():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _case(Path(tmp))
        forgery = _verify(fixture, signature_key=Ed25519PrivateKey.generate())
        assert not forgery["single_manifest_signature_verified_for_supplied_key"]
        assert not forgery["observation_complete"]
        stale = _verify(fixture, high_water=43)
        assert stale["single_manifest_signature_verified_for_supplied_key"]
        assert not stale["generation_floor_satisfied"]
        assert not stale["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert not stale["observation_complete"]


@pytest.mark.parametrize("alteration", [
    {"host_id": "wrong-host"},
    {"schema": "x1.root-efi-custody-pair.v1"},
    {"generation": True},
    {"generation": 42.5},
    {"backup_set_id": "too-short"},
    {"disk_bytes": True},
    {"disk_bytes": 500},
    {"disk_sha256": "invalid-digest"},
    {"unknown": "extra"},
])
def test_wrong_scope_or_signed_metadata_rejected(alteration):
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _case(Path(tmp))
        meta = {**fixture[5], **alteration}
        result = _verify(fixture, manifest_data=_canonical(meta))
        assert not result["observation_complete"]
        assert result["decision"] == "HOLD"


def test_duplicate_json_key_or_noncanonical_manifest_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _case(Path(tmp))
        original = _canonical(fixture[5])
        duplicate = original[:-1] + b',"host_id":"x1-370"}'
        for raw in (duplicate, json.dumps(fixture[5], indent=2).encode()):
            result = _verify(fixture, manifest_data=raw)
            assert not result["observation_complete"]
            assert result["decision"] == "HOLD"


def test_symlink_disk_and_alias_to_existing_image_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _case(Path(tmp))
        media, manifest, root, efi, disk, *_ = fixture
        alias = media / "raw-linked.img"
        alias.symlink_to(disk)
        result = _verify(fixture, disk_image=alias)
        assert not result["observation_complete"]
        assert not result["production_repair_or_reboot_permitted"]
        result = _verify(fixture, disk_image=root)
        assert not result["observation_complete"]


def test_cli_optin_and_exit_always_hold(capsys, tmp_path):
    fixture = _case(tmp_path)
    media, manifest, root, efi, disk, meta, private, trusted = fixture
    with pytest.raises(SystemExit):
        main([])
    capsys.readouterr()
    args = [
        "--explicit-independent-offhost-readonly",
        "--media-root", str(media), "--manifest", str(manifest),
        "--root-image", str(root), "--efi-image", str(efi),
        "--disk-image", str(disk),
        "--signature-hex", private.sign(manifest.read_bytes()).hex(),
        "--trusted-public-key-hex", trusted,
        "--high-water-generation", "42",
    ]
    assert main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "HOLD"
    assert not result["production_repair_or_reboot_permitted"]
    assert trusted not in json.dumps(result)
