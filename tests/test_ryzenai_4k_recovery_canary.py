"""Fail-closed unit tests: no runtime import, device access or model execution."""

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "research" / "ryzenai_4k_recovery_canary.py"
spec = importlib.util.spec_from_file_location("ryzenai_research_canary_test", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture(root: Path):
    model = root / "model"
    model.mkdir()
    config = {
        "model": {
            "type": "qwen2",
            "decoder": {
                "session_options": {
                    "provider_options": [{"RyzenAI": {"hybrid_opt_token_backend": "npu"}}],
                    "custom_ops_library": "/virtual/libonnxruntime_providers_ryzenai.so",
                    "vaip_config": "/virtual/vaip_config.json",
                }
            },
        }
    }
    raw = json.dumps(config, sort_keys=True).encode()
    (model / "genai_config.json").write_bytes(raw)
    (model / "model.onnx").write_bytes(b"x")
    (model / "model.onnx.data").write_bytes(b"data")
    (model / "README.md").write_text("# Qwen_rai_1.8.0_npu_4K\n")
    return model, config, hashlib.sha256(raw).hexdigest()


class RecoveryGateTests(unittest.TestCase):
    def test_candidate_is_always_production_hold(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            result = mod.inspect(path, expected_hash=digest)
            self.assertEqual(result["production_gate"], "HOLD")
            self.assertFalse(result["production_routing_eligible"])
            self.assertIsNone(result["actual_generated_tokens"])
            self.assertNotIn("/virtual", str(result))

    def test_altered_config_digest_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, _ = fixture(Path(root))
            with self.assertRaises(mod.ResearchGateError):
                mod.inspect(path)

    def test_missing_custom_ops_key_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, cfg, _ = fixture(Path(root))
            del cfg["model"]["decoder"]["session_options"]["custom_ops_library"]
            raw = json.dumps(cfg).encode()
            (path / "genai_config.json").write_bytes(raw)
            with self.assertRaises(mod.ResearchGateError):
                mod.inspect(path, expected_hash=hashlib.sha256(raw).hexdigest())

    def test_cpu_provider_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path, cfg, _ = fixture(Path(root))
            cfg["model"]["decoder"]["session_options"]["provider_options"] = [{"CPU": {}}]
            raw = json.dumps(cfg).encode()
            (path / "genai_config.json").write_bytes(raw)
            with self.assertRaises(mod.ResearchGateError):
                mod.inspect(path, expected_hash=hashlib.sha256(raw).hexdigest())

    def test_onnx_external_data_symlink_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "model.onnx.data").unlink()
            (path / "model.onnx.data").symlink_to(path / "model.onnx")
            with self.assertRaises(mod.ResearchGateError):
                mod.inspect(path, expected_hash=digest)

    def test_duplicate_json_key_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, _ = fixture(Path(root))
            raw = b'{"model":{},"model":{}}'
            (path / "genai_config.json").write_bytes(raw)
            with self.assertRaises(mod.ResearchGateError):
                mod.inspect(path, expected_hash=hashlib.sha256(raw).hexdigest())

    def test_default_no_execution_needs_exact_known_hash(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, _ = fixture(Path(root))
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                rc = mod.main(["--model-dir", str(path)])
            self.assertEqual(rc, 3)
            self.assertNotIn(str(path), output.getvalue())

    def test_explicit_ack_required_for_run(self):
        with self.assertRaises(SystemExit) as caught:
            mod.main(["--model-dir", "/tmp/nope", "--run-canary"])
        self.assertEqual(caught.exception.code, 2)

    def test_no_runtime_execution_in_inspection(self):
        source = SCRIPT.read_text()
        self.assertNotIn("import onnxruntime_genai as og\n\nclass", source)
        self.assertIn("production_routing_eligible", source)
        self.assertIn("start_new_session=True", source)

    def test_too_many_tokens_denied_before_host_probe(self):
        with self.assertRaises(mod.ResearchGateError):
            mod.execute(Path("/tmp/notreal"), Path("/tmp/novenv"), 17)


if __name__ == "__main__":
    unittest.main()

