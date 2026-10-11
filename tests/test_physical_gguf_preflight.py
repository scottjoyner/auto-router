"""No-network fake-process tests for the operator-only physical GGUF preflight."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "physical_gguf_preflight", ROOT / "experiments" / "physical_gguf_preflight.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class TestPhysicalGgufPreflight(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.model = self.root / "approved.gguf"
        self.model.write_bytes(b"GGUF" * 111)
        self.checksum = hashlib.sha256(self.model.read_bytes()).hexdigest()

    def fake_proc(self, pid: int, *, model: Path | None = None,
                  context: int = 8192, parallel: int = 4):
        p = self.proc / str(pid)
        p.mkdir(exist_ok=True)
        args = [
            "/usr/local/bin/llama-server", "-m",
            str(model or self.model), "--ctx-size", str(context),
            "--parallel", str(parallel),
        ]
        (p / "cmdline").write_bytes(b"\0".join(x.encode() for x in args) + b"\0")

    def check(self, inventory=lambda *_: (1, 1), **override):
        kwargs = dict(
            expected_sha256=self.checksum,
            expected_bytes=self.model.stat().st_size,
            expected_context=8192, expected_model="liquid/lfm2.5-1.2b",
            proc_root=self.proc, inventory_reader=inventory,
        )
        kwargs.update(override)
        return M.observe(**kwargs)

    def test_exact_single_process_inventory_can_pass_physical_preflight(self):
        self.fake_proc(123)
        r = self.check()
        self.assertEqual(r["status"], "PHYSICAL_PRECHECK_PASSED")
        self.assertEqual(r["matching_process_pid"], 123)
        self.assertEqual(r["configured_context"], 8192)
        self.assertEqual(r["configured_parallel_slots"], 4)
        self.assertEqual(r["expected_model_sha256"], self.checksum)
        self.assertFalse(r["canonical_approval_created"])
        self.assertFalse(r["can_auto_renew"])
        self.assertFalse(r["can_dispatch"])
        self.assertFalse(r["host_signed_execution_witness"])

    def test_wrong_file_hash_refused(self):
        self.fake_proc(123)
        self.assertEqual(self.check(expected_sha256="0" * 64)["status"], "BLOCKED")

    def test_wrong_context_refused(self):
        self.fake_proc(123, context=4096)
        self.assertEqual(self.check()["status"], "BLOCKED")

    def test_no_loaded_model_catalog_refused(self):
        self.fake_proc(123)
        self.assertEqual(self.check(inventory=lambda *_: (0, 0))["status"], "BLOCKED")

    def test_other_loaded_model_makes_result_ambiguous(self):
        self.fake_proc(123)
        self.assertEqual(self.check(inventory=lambda *_: (2, 1))["status"], "BLOCKED")

    def test_process_without_file_refused(self):
        self.fake_proc(123, model=self.root / "missing.gguf")
        self.assertEqual(self.check()["status"], "BLOCKED")

    def test_two_processes_claiming_same_model_refused(self):
        self.fake_proc(123)
        self.fake_proc(124)
        r = self.check()
        self.assertEqual(r["physical_model_process_count"], 2)
        self.assertEqual(r["status"], "BLOCKED")

    def test_second_different_server_not_trusted_as_model_process(self):
        self.fake_proc(123)
        other = self.root / "another.gguf"
        other.write_bytes(b"GGUF" * 7)
        self.fake_proc(124, model=other)
        r = self.check()
        self.assertEqual(r["physical_model_process_count"], 1)
        self.assertEqual(r["other_llama_server_processes"], 1)
        self.assertEqual(r["status"], "PHYSICAL_PRECHECK_PASSED")

    def test_input_hash_must_be_exact(self):
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.check(expected_sha256="BAD")

    def test_negative_or_unbounded_model_size_rejected(self):
        with self.assertRaises(ValueError):
            self.check(expected_bytes=M.MAX_MODEL_BYTES + 1)

    def test_fake_process_with_ambiguous_models_not_accepted(self):
        self.fake_proc(123)
        cmd = self.proc / "123" / "cmdline"
        cmd.write_bytes(cmd.read_bytes() + b"--model\0/another.gguf\0")
        self.assertEqual(self.check()["status"], "BLOCKED")

    def test_inventory_exception_fails_closed(self):
        self.fake_proc(123)
        def unavailable(*_):
            raise OSError("unreachable")
        r = self.check(inventory=unavailable)
        self.assertFalse(r["local_inventory_accessible"])
        self.assertEqual(r["status"], "BLOCKED")

    def test_symlink_model_path_refused(self):
        self.fake_proc(123, model=self.root / "symlink.gguf")
        (self.root / "symlink.gguf").symlink_to(self.model)
        self.assertEqual(self.check()["status"], "BLOCKED")

    def test_inventory_only_connects_to_exact_loopback_endpoint(self):
        with self.assertRaisesRegex(ValueError, "localhost|local"):
            M.local_inventory("http://192.0.2.1:1234/api/v0/models", "model")

    def test_fake_model_cli_cannot_be_exec_receipt(self):
        self.fake_proc(123)
        r = self.check()
        self.assertEqual(r["source"], "unsigned_local_proc_and_inventory")
        self.assertFalse(r["fresh_request_ownership_proven"])
        self.assertFalse(r["network_path_canaries_passed"])
        self.assertTrue(r["operator_review_required"])

    def test_unsigned_lan_and_tailscale_canary_metadata_never_grants_approval(self):
        path = ROOT / "experiments" / "evidence" / "beelink-physical-preflight-20261011.json"
        evidence = json.loads(path.read_text())
        self.assertEqual(evidence["evidence_kind"],
                         "unsigned_operator_observed_physical_preflight")
        self.assertEqual(evidence["matching_process_count"], 1)
        self.assertEqual(evidence["inventory_loaded_count"], 1)
        self.assertEqual({x["transport"] for x in evidence["direct_canaries"]},
                         {"lan", "tailscale"})
        self.assertTrue(all(x["http"] == 200 and x["exact_expected_marker"]
                            and x["max_tokens"] <= 24
                            for x in evidence["direct_canaries"]))
        self.assertFalse(evidence["independent_host_signature"])
        self.assertFalse(evidence["trusted_owner_receipt"])
        self.assertFalse(evidence["current_canonical_approval"])
        self.assertFalse(evidence["can_auto_renew"])
        self.assertFalse(evidence["can_dispatch"])
        self.assertTrue(evidence["must_obtain_operator_reapproval"])
        raw = path.read_text()
        self.assertNotIn("192.168.", raw)
        self.assertNotIn("100.64.", raw)
        self.assertNotIn("Bearer ", raw)

    def test_root_never_contains_raw_model_path_or_secrets(self):
        self.fake_proc(123)
        raw = json.dumps(self.check())
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn("Bearer ", raw)


if __name__ == "__main__":
    unittest.main()
