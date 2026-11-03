"""Offline bundle checks must always refuse automatic installation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "research/x1_offline_pkg_bundle_audit.py"
SPEC = importlib.util.spec_from_file_location("x1_bundle_audit", SOURCE)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture() -> dict:
    return {
        "apt_simulation_sha256_expected": audit.EXPECTED_PLAN_SHA256,
        "packages": {
            name: {
                "name": name,
                "file": f"{name}_{version}_{arch}.deb",
                "size": 0,
                "version": version,
                "architecture": arch,
                "sha256": "0" * 64,
            }
            for name, (version, arch) in audit.EXPECTED_PACKAGES.items()
        },
    }


def evaluate(data: dict):
    with tempfile.TemporaryDirectory() as tmp:
        content = json.dumps(data).encode()
        return audit.audit_manifest(Path(tmp), content)


class OfflineBundleTests(unittest.TestCase):
    def test_untrusted_manifest_fails_closed(self):
        report = evaluate(fixture())
        self.assertFalse(report["bundle_verified"])
        self.assertIn("untrusted_manifest_digest", report["hold_reasons"])
        self.assertTrue(report["blocked"])
        self.assertFalse(report["may_install_packages"])

    def test_missing_package_fails_closed(self):
        data = fixture()
        del data["packages"]["libc6"]
        report = evaluate(data)
        self.assertIn("unexpected_package_manifest_set", report["hold_reasons"])

    def test_extra_package_fails_closed(self):
        data = fixture()
        data["packages"]["unexpected"] = {}
        self.assertIn("unexpected_package_manifest_set", evaluate(data)["hold_reasons"])

    def test_wrong_plan_identity_fails(self):
        data = fixture()
        data["apt_simulation_sha256_expected"] = "wrong"
        self.assertIn("unexpected_apt_plan_identity", evaluate(data)["hold_reasons"])

    def test_untrusted_filename_never_used(self):
        data = fixture()
        data["packages"]["libc6"]["file"] = "../../etc/passwd"
        report = evaluate(data)
        self.assertIn("metadata_mismatch_libc6", report["hold_reasons"])

    def test_symlink_is_rejected(self):
        data = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            name = "libc6_2.43-2ubuntu2.4_amd64.deb"
            (directory / name).symlink_to("/etc/passwd")
            with patch.object(
                audit, "MANIFEST_SHA256", hashlib.sha256(json.dumps(data).encode()).hexdigest()
            ):
                report = audit.audit_manifest(directory, json.dumps(data).encode())
            self.assertIn("package_verification_failed_libc6", report["hold_reasons"])

    def test_invalid_json_fails_closed(self):
        report = audit.audit_manifest(Path("/tmp"), b"not-json")
        self.assertFalse(report["bundle_verified"])
        self.assertIn("invalid_manifest_json", report["hold_reasons"])

    def test_pinned_digest_can_be_checked_without_installing(self):
        data = fixture()
        payload = json.dumps(data).encode()
        with patch.object(audit, "MANIFEST_SHA256", hashlib.sha256(payload).hexdigest()):
            report = audit.audit_manifest(Path("/nonexistent"), payload)
        self.assertFalse(report["bundle_verified"])
        self.assertFalse(report["may_install_packages"])
        self.assertEqual(report["verified_package_count"], 0)


if __name__ == "__main__":
    unittest.main()
