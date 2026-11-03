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


def inspect_fixture(path: Path, digest: str, *, expected_manifest: str | None = None) -> dict:
    return mod.inspect(
        path,
        expected_hash=digest,
        expected_graph_hash=hashlib.sha256(b"x").hexdigest(),
        expected_data_hash=hashlib.sha256(b"data").hexdigest(),
        expected_manifest_hash=(
            expected_manifest if expected_manifest is not None else FIXTURE_MANIFEST_SHA256
        ),
    )


with tempfile.TemporaryDirectory() as _fixture_root:
    _fixture_path, _, _ = fixture(Path(_fixture_root))
    FIXTURE_MANIFEST_SHA256 = mod._package_manifest(_fixture_path)[0]


class RecoveryGateTests(unittest.TestCase):
    def test_candidate_is_always_production_hold(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            result = inspect_fixture(path, digest)
            self.assertEqual(result["production_gate"], "HOLD")
            self.assertFalse(result["production_routing_eligible"])
            self.assertIsNone(result["actual_generated_tokens"])
            self.assertNotIn("/virtual", str(result))

    def test_decode_phase_requires_successful_npu_ioctl(self):
        trace = [
            "99 ioctl(3</dev/accel/accel0>, ..., 0x0) = 0",
            '99 write(2<pipe:[12]>, "RYZENAI_GENERATION_BEGIN\\n", 25) = 25',
            "99 ioctl(3</dev/accel/accel0>, ..., 0x0) = 0",
            "99 ioctl(3</dev/accel/accel0>, ..., 0x0) = -1 EFAULT",
            "99 ioctl(3</dev/null>, ..., 0x0) = 0",
            '99 write(2<pipe:[12]>, "RYZENAI_GENERATION_END\\n", 23) = 23',
            "99 ioctl(3</dev/accel/accel0>, ..., 0x0) = 0",
        ]
        self.assertEqual(mod._generation_phase_npu_ioctls(trace), 1)

    def test_decode_phase_rejects_init_only_npu_work(self):
        trace = [
            "99 ioctl(3</dev/accel/accel0>, ..., 0x0) = 0",
            '99 write(2<pipe:[12]>, "RYZENAI_GENERATION_BEGIN\\n", 25) = 25',
            "99 ioctl(3</dev/accel/accel0>, ..., 0x0) = -1 EFAULT",
            '99 write(2<pipe:[12]>, "RYZENAI_GENERATION_END\\n", 23) = 23',
        ]
        with self.assertRaisesRegex(mod.ResearchGateError, "no successful NPU ioctl"):
            mod._generation_phase_npu_ioctls(trace)

    def test_decode_phase_rejects_missing_or_duplicate_markers(self):
        begin = '99 write(2<pipe:[12]>, "RYZENAI_GENERATION_BEGIN\\n", 25) = 25'
        end = '99 write(2<pipe:[12]>, "RYZENAI_GENERATION_END\\n", 23) = 23'
        ioctl = "99 ioctl(3</dev/accel/accel0>, ..., 0x0) = 0"
        for trace in (
            [ioctl],
            [end, ioctl, begin],
            [begin, begin, ioctl, end],
            [begin, ioctl, end, end],
        ):
            with self.subTest(trace=trace), self.assertRaises(mod.ResearchGateError):
                mod._generation_phase_npu_ioctls(trace)

    def test_model_graph_mutation_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "model.onnx").write_bytes(b"tampered")
            with self.assertRaisesRegex(mod.ResearchGateError, "graph fingerprint"):
                inspect_fixture(path, digest)

    def test_external_weights_mutation_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "model.onnx.data").write_bytes(b"tampered")
            with self.assertRaisesRegex(mod.ResearchGateError, "tensor fingerprint"):
                inspect_fixture(path, digest)

    def test_package_metadata_mutation_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "README.md").write_text("# Qwen_rai_1.8.0_npu_4K\nmodified")
            with self.assertRaisesRegex(mod.ResearchGateError, "full package manifest"):
                inspect_fixture(path, digest)

    def test_opaque_compiled_file_mutation_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "opaque.ctrlpkt").write_bytes(b"malicious")
            with self.assertRaisesRegex(mod.ResearchGateError, "full package manifest"):
                inspect_fixture(path, digest)

    def test_subdirectory_entry_mutation_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "extra").mkdir()
            with self.assertRaisesRegex(mod.ResearchGateError, "full package manifest"):
                inspect_fixture(path, digest)

    def test_symlink_package_entry_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "extra.ctrlpkt").symlink_to(path / "model.onnx")
            with self.assertRaisesRegex(mod.ResearchGateError, "symlink package entry"):
                inspect_fixture(path, digest)

    def test_manifest_is_order_independent(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            self.assertEqual(mod._package_manifest(path)[0], FIXTURE_MANIFEST_SHA256)
            self.assertEqual(
                inspect_fixture(path, digest)["full_package_manifest_sha256"],
                FIXTURE_MANIFEST_SHA256,
            )

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
                inspect_fixture(path, hashlib.sha256(raw).hexdigest())

    def test_cpu_provider_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path, cfg, _ = fixture(Path(root))
            cfg["model"]["decoder"]["session_options"]["provider_options"] = [{"CPU": {}}]
            raw = json.dumps(cfg).encode()
            (path / "genai_config.json").write_bytes(raw)
            with self.assertRaises(mod.ResearchGateError):
                inspect_fixture(path, hashlib.sha256(raw).hexdigest())

    def test_onnx_external_data_symlink_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, digest = fixture(Path(root))
            (path / "model.onnx.data").unlink()
            (path / "model.onnx.data").symlink_to(path / "model.onnx")
            with self.assertRaises(mod.ResearchGateError):
                inspect_fixture(path, digest)

    def test_duplicate_json_key_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, _ = fixture(Path(root))
            raw = b'{"model":{},"model":{}}'
            (path / "genai_config.json").write_bytes(raw)
            with self.assertRaises(mod.ResearchGateError):
                inspect_fixture(path, hashlib.sha256(raw).hexdigest())

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
