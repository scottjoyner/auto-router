"""Synthetic-only read-only clone copy checking; never grants a boot/repair."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest

from scripts.verify_x1_offline_clone_copy import inspect_clone, main

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "x1_gpt_fixture", SOURCE / "tests" / "test_x1_offline_gpt_mirror.py",
)
assert spec and spec.loader
gpt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpt)


def _fixture(tmp):
    media = Path(tmp) / "offline"
    media.mkdir()
    target = media / "restored-clone.raw"
    image = bytes(gpt.disk_fixture())
    target.write_bytes(image)
    return media, target, image


def test_exact_copy_validates_bytes_and_structure_but_remains_hold():
    with tempfile.TemporaryDirectory() as tmp:
        media, target, image = _fixture(tmp)
        result = inspect_clone(
            media_root=media, clone_image=target,
            authenticated_disk_sha256=hashlib.sha256(image).hexdigest(),
            authenticated_disk_bytes=len(image),
        )
        assert result["observation_complete"]
        assert result["clone_size_and_sha256_match_pinned_source"]
        assert result["clone_gpt_structurally_consistent"]
        assert not result["physical_restoration_proven"]
        assert not result["firmware_boot_and_os_userspace_proven"]
        assert not result["live_x1_maintenance_authorized"]
        assert result["decision"] == "HOLD"
        assert str(media) not in json.dumps(result)


def test_modified_clone_does_not_match_pinned_source():
    with tempfile.TemporaryDirectory() as tmp:
        media, target, image = _fixture(tmp)
        changed = bytearray(image)
        changed[170_000] ^= 0xFF
        target.write_bytes(changed)
        result = inspect_clone(
            media_root=media, clone_image=target,
            authenticated_disk_sha256=hashlib.sha256(image).hexdigest(),
            authenticated_disk_bytes=len(image),
        )
        assert result["observation_complete"]
        assert not result["clone_size_and_sha256_match_pinned_source"]
        assert not result["clone_gpt_structurally_consistent"]


def test_signed_but_invalid_gpt_clone_remains_hold():
    with tempfile.TemporaryDirectory() as tmp:
        media, target, image = _fixture(tmp)
        changed = bytearray(image)
        changed[512] ^= 0xFF
        target.write_bytes(changed)
        result = inspect_clone(
            media_root=media, clone_image=target,
            authenticated_disk_sha256=hashlib.sha256(changed).hexdigest(),
            authenticated_disk_bytes=len(changed),
        )
        assert result["clone_size_and_sha256_match_pinned_source"]
        assert not result["clone_gpt_structurally_consistent"]
        assert result["decision"] == "HOLD"


def test_symlink_and_wrong_offline_root_deny():
    with tempfile.TemporaryDirectory() as tmp:
        media, target, image = _fixture(tmp)
        link = media / "linked.raw"
        link.symlink_to(target)
        for root, path in ((media, link), (Path("/dev"), target), (Path("/"), target)):
            report = inspect_clone(
                media_root=root, clone_image=path,
                authenticated_disk_sha256=hashlib.sha256(image).hexdigest(),
                authenticated_disk_bytes=len(image),
            )
            assert not report["observation_complete"]
            assert not report["live_x1_maintenance_authorized"]


@pytest.mark.parametrize(("sha", "size"), [
    ("x" * 64, 1048576),
    ("0" * 64, True),
    ("0" * 64, -1),
    ("0" * 64, 1023),
    ("0" * 64, 1_048_575),
])
def test_bad_expected_identity_denied(sha, size):
    with tempfile.TemporaryDirectory() as tmp:
        media, target, image = _fixture(tmp)
        report = inspect_clone(
            media_root=media, clone_image=target,
            authenticated_disk_sha256=sha, authenticated_disk_bytes=size,
        )
        assert not report["observation_complete"]
        assert report["decision"] == "HOLD"


def test_optin_and_exit_always_hold(capsys):
    with pytest.raises(SystemExit):
        main([])
    capsys.readouterr()
    with tempfile.TemporaryDirectory() as tmp:
        media, target, image = _fixture(tmp)
        code = main([
            "--explicit-independent-offhost-readonly",
            "--media-root", str(media), "--clone-image", str(target),
            "--authenticated-disk-sha256", hashlib.sha256(image).hexdigest(),
            "--authenticated-disk-bytes", str(len(image)),
        ])
        assert code == 2
        result = json.loads(capsys.readouterr().out)
        assert result["decision"] == "HOLD"
