"""Offline aggregation tests; never promote signed/GPT observations to repair."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "x1_gate_under_test", ROOT / "scripts" / "assess_x1_offline_recovery_gate.py",
)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

PRIVATE_SIGNATURE = "1" * 128
PUBLIC_KEY_HEX = "2" * 64
MEDIA = Path("/mnt/synthetic-offhost-media")
MANIFEST = MEDIA / "manifest.json"
ROOT_IMAGE = MEDIA / "root.img"
EFI_IMAGE = MEDIA / "efi.img"
DISK_IMAGE = MEDIA / "disk.raw"


def _call(**overrides):
    inputs = {
        "media_root": MEDIA,
        "manifest": MANIFEST,
        "root_image": ROOT_IMAGE,
        "efi_image": EFI_IMAGE,
        "disk_image": DISK_IMAGE,
        "signature_hex": PRIVATE_SIGNATURE,
        "trusted_public_key_hex": PUBLIC_KEY_HEX,
        "high_water_generation": 9,
    }
    inputs.update(overrides)
    return gate.assess(**inputs)


def _patch(monkeypatch, *, witness_ok=True, disk_ok=True):
    seen = []
    def mock_witness(**kw):
        seen.append(("witness", kw))
        return {
            "signature_verified_for_supplied_public_key": witness_ok,
            "generation_floor_satisfied": witness_ok,
            "root_efi_signed_digests_and_sizes_match": witness_ok,
            "observation_complete": witness_ok,
        }
    def mock_disk(media_root, disk_image):
        seen.append(("disk", {"media_root": media_root, "image": disk_image}))
        return {
            "primary_backup_gpt_and_array_match": disk_ok,
            "observation_complete": disk_ok,
        }
    monkeypatch.setattr(gate, "verify", mock_witness)
    monkeypatch.setattr(gate, "inspect", mock_disk)
    return seen


def test_two_independent_checks_run_against_explicit_selected_media(monkeypatch):
    seen = _patch(monkeypatch)
    result = _call()
    assert len(seen) == 2
    assert seen[0][0] == "witness"
    assert seen[0][1]["manifest"] == MANIFEST
    assert seen[0][1]["root_image"] == ROOT_IMAGE
    assert seen[0][1]["efi_image"] == EFI_IMAGE
    assert seen[0][1]["trusted_public_key_hex"] == PUBLIC_KEY_HEX
    assert seen[0][1]["high_water_generation"] == 9
    assert seen[1] == (
        "disk", {"media_root": MEDIA, "image": DISK_IMAGE},
    )
    assert result["checks"]["root_efi_signature_highwater_and_hashes_valid"]
    assert result["checks"]["whole_disk_primary_backup_gpt_semantics_valid"]
    assert not result["checks"]["whole_disk_linked_to_signed_root_efi_pair"]
    assert not result["checks"]["signing_key_custody_independently_proven"]
    assert not result["checks"]["tested_clone_restored_on_independent_media"]
    assert not result["checks"]["uefi_console_boot_to_verified_userspace_witnessed"]
    assert not result["production_repair_or_reboot_permitted"]
    assert result["decision"] == "HOLD"
    serialized = json.dumps(result)
    assert PRIVATE_SIGNATURE not in serialized
    assert PUBLIC_KEY_HEX not in serialized
    assert str(MEDIA) not in serialized


@pytest.mark.parametrize(("witness_ok", "disk_ok"), [
    (True, False), (False, True), (False, False),
])
def test_incomplete_observation_is_reported_not_silently_promoted(
    monkeypatch, witness_ok, disk_ok,
):
    _patch(monkeypatch, witness_ok=witness_ok, disk_ok=disk_ok)
    result = _call()
    assert result["signed_root_efi_observation_complete"] is witness_ok
    assert result["raw_disk_observation_complete"] is disk_ok
    assert result["decision"] == "HOLD"
    assert result["production_repair_or_reboot_permitted"] is False
    assert bool(result["unmet_evidence"])


def test_missing_witness_fields_fail_closed(monkeypatch):
    monkeypatch.setattr(gate, "verify", lambda **kw: {
        "signature_verified_for_supplied_public_key": True,
        "generation_floor_satisfied": True,
        # Intentionally no digest or observation evidence.
    })
    monkeypatch.setattr(gate, "inspect", lambda *_: {
        "primary_backup_gpt_and_array_match": True,
        "observation_complete": True,
    })
    result = _call()
    assert not result["checks"]["root_efi_signature_highwater_and_hashes_valid"]
    assert not result["production_repair_or_reboot_permitted"]


def test_cli_requires_explicit_isolated_offhost_optin(capsys):
    with pytest.raises(SystemExit) as exc:
        gate.main([])
    assert exc.value.code != 0
    assert not capsys.readouterr().out


def test_cli_reports_no_secrets_and_never_returns_zero(monkeypatch, capsys):
    _patch(monkeypatch)
    code = gate.main([
        "--explicit-independent-offhost-readonly",
        "--media-root", str(MEDIA),
        "--manifest", str(MANIFEST),
        "--root-image", str(ROOT_IMAGE),
        "--efi-image", str(EFI_IMAGE),
        "--disk-image", str(DISK_IMAGE),
        "--signature-hex", PRIVATE_SIGNATURE,
        "--trusted-public-key-hex", PUBLIC_KEY_HEX,
        "--high-water-generation", "9",
    ])
    assert code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "HOLD"
    assert result["production_repair_or_reboot_permitted"] is False
