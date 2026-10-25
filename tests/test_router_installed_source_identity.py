"""No-network security acceptance for installed Auto-Router source identity checks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "router_installed_source_identity",
    ROOT / "scripts" / "router_installed_source_identity.py",
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SAFE_PROVIDER = b"# safe synthetic adapter\nclass Provider: pass\n"
SAFE_DISPATCHER = b"# AssistX event delivery transport failure\n"
SAFE_CONFIG = b"# LMSTUDIO_XWING_API_KEY_FILE\n"


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="router-id-test-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.pkg = root / "auto_router"
        self.pkg.mkdir()
        self.config = root / "providers.yaml"
        (self.pkg / "providers.py").write_bytes(SAFE_PROVIDER)
        (self.pkg / "event_dispatcher.py").write_bytes(SAFE_DISPATCHER)
        self.config.write_bytes(SAFE_CONFIG)
        self.pins = self.current()

    def current(self):
        return {
            "providers.py": hashlib.sha256((self.pkg / "providers.py").read_bytes()).hexdigest(),
            "event_dispatcher.py": hashlib.sha256((self.pkg / "event_dispatcher.py").read_bytes()).hexdigest(),
            "providers.yaml": hashlib.sha256(self.config.read_bytes()).hexdigest(),
        }

    def verify(self):
        return mod.inspect(self.pkg, self.config, self.pins)

    def hold(self, reason):
        result = self.verify()
        self.assertEqual(result["verdict"], "HOLD")
        self.assertEqual(result["reason"], reason)
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["asgi_lifespan_started"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["archive_writes"], 0)
        self.assertNotIn("SECRET_FIXTURE", json.dumps(result))
        return result

    def test_good_exact_identity_is_not_deploy_permission(self):
        result = self.verify()
        self.assertEqual(result["verdict"], "SOURCE_IDENTITY_MATCH_ONLY")
        self.assertTrue(result["installed_module_identity_matched"])
        self.assertTrue(result["effective_config_identity_matched"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["deployed_image_provenance_verified"])

    def test_old_provider_echo_always_denied_even_with_matching_hash(self):
        (self.pkg / "providers.py").write_bytes(SAFE_PROVIDER + b"response.text[:500]\n")
        self.pins = self.current()
        self.hold("upstream_error_echo_marker_present")

    def test_old_dispatcher_echo_always_denied_even_with_matching_hash(self):
        (self.pkg / "event_dispatcher.py").write_bytes(SAFE_DISPATCHER + b"response.text[:500]\n")
        self.pins = self.current()
        self.hold("upstream_error_echo_marker_present")

    def test_missing_sanitized_event_marker_denied(self):
        (self.pkg / "event_dispatcher.py").write_bytes(b"class NoMarker: pass\n")
        self.pins = self.current()
        self.hold("sanitized_event_error_marker_absent")

    def test_missing_xwing_token_file_marker_denied(self):
        self.config.write_bytes(b"providers: []\n")
        self.pins = self.current()
        self.hold("xwing_optional_token_file_marker_absent")

    def test_provider_digest_drift_denied(self):
        (self.pkg / "providers.py").write_bytes(SAFE_PROVIDER + b"# changed\n")
        self.hold("installed_source_digest_mismatch")

    def test_dispatcher_digest_drift_denied(self):
        (self.pkg / "event_dispatcher.py").write_bytes(SAFE_DISPATCHER + b"# changed\n")
        self.hold("installed_source_digest_mismatch")

    def test_effective_mounted_config_drift_denied(self):
        self.config.write_bytes(SAFE_CONFIG + b"# changed\n")
        result = self.hold("effective_config_digest_mismatch")
        self.assertTrue(result["installed_module_identity_matched"])

    def test_missing_provider_file_denied(self):
        (self.pkg / "providers.py").unlink()
        self.hold("path_unavailable")

    def test_symlink_file_denied(self):
        target = self.pkg / "actual-provider.py"
        (self.pkg / "providers.py").rename(target)
        (self.pkg / "providers.py").symlink_to(target)
        self.hold("symlink_path")

    def test_symlink_package_parent_denied(self):
        other = self.pkg.with_name("other-package")
        self.pkg.rename(other)
        self.pkg.symlink_to(other, target_is_directory=True)
        self.hold("symlink_path")

    def test_fifo_rejected_without_writer(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("No FIFO on this OS")
        p = self.pkg / "providers.py"
        p.unlink()
        os.mkfifo(p, 0o600)
        self.hold("file_not_regular_or_bounded")

    def test_group_writable_module_denied(self):
        (self.pkg / "providers.py").chmod(0o664)
        self.hold("file_group_or_other_writable")

    def test_large_module_denied(self):
        (self.pkg / "providers.py").write_bytes(b"x" * (mod.MAX_CODE_BYTES + 1))
        self.hold("file_not_regular_or_bounded")

    def test_bad_pin_format_denied(self):
        self.pins["providers.py"] = "not_a_hash"
        self.hold("invalid_sha256_pin")

    def test_missing_pin_denied(self):
        del self.pins["providers.yaml"]
        self.hold("missing_pin")

    def test_relative_config_denied(self):
        self.config = Path("providers.yaml")
        self.hold("unqualified_path")

    def test_cli_returns_success_only_for_identity_not_authority(self):
        import io
        with patch.object(mod, "package_root", return_value=self.pkg), \
             patch.object(sys, "argv", [
                "src-id", "--providers-sha256", self.pins["providers.py"],
                "--dispatcher-sha256", self.pins["event_dispatcher.py"],
                "--config-sha256", self.pins["providers.yaml"],
                "--config-path", str(self.config),
             ]), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            result = mod.main()
        self.assertEqual(result, 0)
        obj = json.loads(stdout.getvalue())
        self.assertEqual(obj["verdict"], "SOURCE_IDENTITY_MATCH_ONLY")
        self.assertFalse(obj["deployment_authorized"])

    def test_cli_returns_nonzero_for_drift(self):
        import io
        (self.pkg / "providers.py").write_bytes(SAFE_PROVIDER + b"#drift\n")
        with patch.object(mod, "package_root", return_value=self.pkg), \
             patch.object(sys, "argv", [
                "src-id", "--providers-sha256", self.pins["providers.py"],
                "--dispatcher-sha256", self.pins["event_dispatcher.py"],
                "--config-sha256", self.pins["providers.yaml"],
                "--config-path", str(self.config),
             ]), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            result = mod.main()
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stdout.getvalue())["reason"], "installed_source_digest_mismatch")


if __name__ == "__main__":
    unittest.main()
