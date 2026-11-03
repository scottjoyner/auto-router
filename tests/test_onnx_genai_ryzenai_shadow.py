"""Synthetic ONNX Runtime GenAI fleet preflight/admission guards (no model loading).

All tests are stdlib-only and load the pure module by file path to avoid
initializing production Auto-Router/AssistX packages or importing ORT.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]/"research"/"onnx_genai_ryzenai_shadow.py"
SPEC=importlib.util.spec_from_file_location("genai_ryzenai_shadow_test",SOURCE)
shadow=importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name]=shadow
SPEC.loader.exec_module(shadow)


def fixture(model_type="qwen2",providers=None):
    if providers is None:
        providers=[{"RyzenAI":{"hybrid_opt_token_backend":"npu"}}]
    return {
        "model":{"type":model_type,"decoder":{"filename":"model.onnx",
                "session_options":{"provider_options":providers}},
                "context_length":32768},
        "search":{"max_length":32},
    }


def payload(**kwargs):
    return json.dumps(fixture(**kwargs),separators=(",",":"),sort_keys=True).encode()


class AdmissionShadowTests(unittest.TestCase):
    def test_upstream_exact_pin(self):
        self.assertEqual(shadow.UPSTREAM_SOURCE_SHA,
                         "82a4438b88febf4dd1762506d6cd5f50c5231ee3")

    def test_qwen2_ryzen_provider_claim_is_not_generation_proof(self):
        r=shadow.parse_config(payload())
        self.assertEqual(r["configured_model_architecture"],"qwen2")
        self.assertTrue(r["ryzenai_declared"])
        self.assertEqual(r["configured_providers"],["RyzenAI"])
        self.assertFalse(r["production_routing_eligible"])
        self.assertFalse(r["actual_generation_verified"])
        self.assertIsNone(r["actual_generated_tokens"])
        self.assertEqual(r["production_gate"],"HOLD")

    def test_upstream_lfm3_type_is_not_installed_runtime_support(self):
        r=shadow.parse_config(payload(model_type="lfm3"))
        self.assertTrue(r["upstream_current_tree_lists_lfm3"])
        self.assertFalse(r["installed_runtime_lfm3_executed"])
        self.assertFalse(r["model_file_load_verified"])

    def test_lfm2_config_does_not_claim_npu_support(self):
        r=shadow.parse_config(payload(model_type="lfm2"))
        self.assertFalse(r["npu_device_initialization_verified"])
        self.assertFalse(r["actual_generation_verified"])

    def test_synthetic_sha_is_deterministic(self):
        raw=payload()
        r=shadow.parse_config(raw)
        self.assertEqual(r["model_config_sha256"],hashlib.sha256(raw).hexdigest())

    def test_input_bytes_cap(self):
        for x in (b"",b"\xff",b"x"*16385, b" " * 20000):
            with self.subTest(size=len(x)),self.assertRaises(shadow.AdmissionShadowError):
                shadow.parse_config(x)

    def test_non_bytes_rejected(self):
        for obj in (None,"{}",True,42,bytearray(b"{}"),{}):
            with self.subTest(value=type(obj).__name__),self.assertRaises(shadow.AdmissionShadowError):
                shadow.parse_config(obj)

    def test_invalid_unicode_rejected(self):
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.parse_config(b"{\xff}")

    def test_nonfinite_json_rejected(self):
        raw=payload().replace(b'"max_length":32',b'"max_length":NaN')
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.parse_config(raw)

    def test_duplicate_json_key_rejected(self):
        raw=payload().replace(b'"type":"qwen2"',b'"type":"lfm3","type":"qwen2"')
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.parse_config(raw)

    def test_bad_root_json_rejected(self):
        for x in (b'null',b'[]',b'5',b'"hello"',b'{}',b'{"model":[]}',b'{malformed}'):
            with self.subTest(value=x[:20]),self.assertRaises(shadow.AdmissionShadowError):
                shadow.parse_config(x)

    def test_wrong_model_type_rejected(self):
        for x in (False,True,[],{},None,0,"","QWEN2","gemma3","../qwen2"):
            cfg=fixture();cfg["model"]["type"]=x
            with self.subTest(value=str(x)),self.assertRaises(shadow.AdmissionShadowError):
                shadow.parse_config(json.dumps(cfg).encode())

    def test_missing_decoder_denied(self):
        cfg=fixture();cfg["model"].pop("decoder")
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.parse_config(json.dumps(cfg).encode())

    def test_missing_session_denied(self):
        cfg=fixture();cfg["model"]["decoder"].pop("session_options")
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.parse_config(json.dumps(cfg).encode())

    def test_malformed_session_rejected(self):
        cfg=fixture();cfg["model"]["decoder"]["session_options"]=[]
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.parse_config(json.dumps(cfg).encode())

    def test_empty_provider_list_is_not_ryzenai(self):
        r=shadow.parse_config(payload(providers=[]))
        self.assertFalse(r["ryzenai_declared"])
        self.assertFalse(r["cpu_fallback_verified"])

    def test_cpu_declared_not_cpu_executed(self):
        r=shadow.parse_config(payload(providers=[{"CPU":{}}]))
        self.assertEqual(r["configured_providers"],["CPU"])
        self.assertFalse(r["cpu_fallback_verified"])

    def test_unknown_provider_never_admitted(self):
        for x in ("Ryzen-AI","cuda","XRT", "NPU", "ExternalPaid"):
            with self.subTest(provider=x),self.assertRaises(shadow.AdmissionShadowError):
                shadow.parse_config(payload(providers=[{x:{}}]))

    def test_invalid_provider_arrays(self):
        for x in (None,True,23,"RyzenAI",{},[None],["RyzenAI"],[{"RyzenAI":None}],
                  [{"RyzenAI":{},"CPU":{}}], [{"RyzenAI":{}},{"RyzenAI":{}}], [{}],
                  [{"CPU":{}}]*9):
            with self.subTest(value=str(x)[:30]),self.assertRaises(shadow.AdmissionShadowError):
                shadow.parse_config(payload(providers=x))

    def test_two_distinct_providers_listed_deterministically(self):
        r=shadow.parse_config(payload(providers=[{"RyzenAI":{}},{"CPU":{}}]))
        self.assertEqual(r["configured_providers"],["CPU","RyzenAI"])

    def test_probe_failure_requires_explicit_code(self):
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.summarize(payload(),failure="../../api-key")

    def test_probe_success_evidence_always_on_hold(self):
        for name in shadow.ERROR_CODES:
            r=shadow.summarize(payload(),failure=name)
            with self.subTest(name=name):
                self.assertFalse(r["production_routing_eligible"])
                self.assertFalse(r["evidence_independent_custody"])
                self.assertEqual(r["production_gate"],"HOLD")

    def test_runtime_provider_missing_is_classified_without_path_leak(self):
        err="RuntimeError: Failed to load library /private/libonnxruntime_providers_ryzenai.so: cannot open shared object file"
        self.assertEqual(shadow.classify_failure(err,exit_code=1),
                         "NPU_PROVIDER_LIBRARY_NOT_FOUND")
        r=str(shadow.summarize(payload(),failure="NPU_PROVIDER_LIBRARY_NOT_FOUND"))
        self.assertNotIn("/private",r)

    def test_npu_ioctl_bad_address_classified(self):
        err="RuntimeError: DRM_IOCTL_AMDXDNA_CREATE_BO IOCTL failed (err=-14): Bad address"
        self.assertEqual(shadow.classify_failure(err,exit_code=1),"NPU_IOCTL_BAD_ADDRESS")

    def test_cpu_invalid_graph_classified(self):
        self.assertEqual(shadow.classify_failure("RuntimeError: This is an invalid model. In Node: If",
                                                 exit_code=1),"CPU_INVALID_GRAPH")

    def test_timeout_classification(self):
        self.assertEqual(shadow.classify_failure("",exit_code=124),"TIMEOUT")
        self.assertEqual(shadow.classify_failure("",exit_code=137),"TIMEOUT")

    def test_zero_exit_code_not_verified(self):
        self.assertEqual(shadow.classify_failure("PASS",exit_code=0),"NO_ATTEMPT")

    def test_no_attempt(self):
        self.assertEqual(shadow.classify_failure(None,exit_code=None),"NO_ATTEMPT")

    def test_unrecognized_failure_does_not_echo_text(self):
        text="SECRET-token-was-sent to /really/private/path"
        name=shadow.classify_failure(text,exit_code=1)
        self.assertEqual(name,"OTHER_FAILURE")
        self.assertNotIn("SECRET",name)
        self.assertNotIn("/really/private",name)

    def test_invalid_exit_codes(self):
        for c in (True,-1,256,"0",None,2.1):
            with self.subTest(v=c),self.assertRaises(shadow.AdmissionShadowError):
                shadow.classify_failure("error",exit_code=c)

    def test_invalid_error_text(self):
        for text in (True,42,None,[],"x"*16385):
            with self.subTest(v=type(text).__name__),self.assertRaises(shadow.AdmissionShadowError):
                shadow.classify_failure(text,exit_code=1)

    def test_invalid_device_hints_rejected(self):
        for hints in ({},{"accel_character_device_present":True},True,None,
                      {"amdxdna_module_present":1,"accel_character_device_present":True,
                       "npu_ioctl_functional":None},
                      {"amdxdna_module_present":True,"accel_character_device_present":True,
                       "npu_ioctl_functional":True}):
            if hints is None:
                continue
            with self.subTest(v=hints),self.assertRaises(shadow.AdmissionShadowError):
                shadow.summarize(payload(),hints=hints)

    def test_character_device_hint_still_not_npu_verification(self):
        with tempfile.TemporaryDirectory() as root:
            driver=Path(root)/"driver";driver.mkdir()
            r=shadow.device_hints(accel_device=Path("/dev/null"),driver=driver)
            self.assertTrue(r["amdxdna_module_present"])
            self.assertTrue(r["accel_character_device_present"])
            self.assertIsNone(r["npu_ioctl_functional"])
            report=shadow.summarize(payload(),hints=r)
            self.assertFalse(report["production_routing_eligible"])

    def test_regular_file_is_not_accel_character(self):
        with tempfile.TemporaryDirectory() as root:
            reg=Path(root)/"device";reg.write_text("not-character-device")
            status=shadow.device_hints(accel_device=reg,driver=Path(root)/"absent")
            self.assertFalse(status["accel_character_device_present"])
            self.assertFalse(status["amdxdna_module_present"])

    def test_parent_symlink_is_not_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"real";p.mkdir()
            (p/"genai_config.json").write_bytes(payload())
            link=Path(root)/"link";link.symlink_to(p)
            with self.assertRaises(shadow.AdmissionShadowError):
                shadow.safe_config_read(link/"genai_config.json")

    def test_file_symlink_not_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"real.json";p.write_bytes(payload())
            link=Path(root)/"genai_config.json";link.symlink_to(p)
            with self.assertRaises(shadow.AdmissionShadowError):
                shadow.safe_config_read(link)

    def test_noncanonical_filename_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"config.json";path.write_bytes(payload())
            with self.assertRaises(shadow.AdmissionShadowError):
                shadow.safe_config_read(path)

    def test_nonabsolute_path_denied(self):
        with self.assertRaises(shadow.AdmissionShadowError):
            shadow.safe_config_read(Path("genai_config.json"))

    def test_regular_config_bytes_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"genai_config.json";path.write_bytes(payload())
            self.assertEqual(shadow.safe_config_read(path),payload())

    def test_oversized_file_denied_without_unbounded_read(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"genai_config.json";path.write_bytes(b" "*16385)
            with self.assertRaises(shadow.AdmissionShadowError):
                shadow.safe_config_read(path)

    def test_directory_disguised_as_config_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"genai_config.json";path.mkdir()
            with self.assertRaises(shadow.AdmissionShadowError):
                shadow.safe_config_read(path)

    def test_cli_refuses_implicit_read(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"genai_config.json";path.write_bytes(payload())
            err=io.StringIO()
            with contextlib.redirect_stderr(err):
                rc=shadow.main(["--config",str(path)])
            self.assertEqual(rc,2)
            self.assertIn("--allow-read",err.getvalue())

    def test_cli_only_emits_safe_summary(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"genai_config.json";path.write_bytes(payload())
            stdout=io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc=shadow.main(["--config",str(path),"--allow-read",
                                "--observed-failure","NPU_IOCTL_BAD_ADDRESS"])
            self.assertEqual(rc,0)
            output=json.loads(stdout.getvalue())
            self.assertEqual(output["observed_failure_class"],"NPU_IOCTL_BAD_ADDRESS")
            self.assertEqual(output["production_gate"],"HOLD")
            self.assertFalse(output["production_routing_eligible"])
            self.assertNotIn(str(path),stdout.getvalue())
            self.assertNotIn("hybrid_opt_token_backend",stdout.getvalue())

    def test_cli_rejects_invalid_json_without_echoing(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"genai_config.json";path.write_text('{"secret":"abc"}')
            stdout=io.StringIO();err=io.StringIO()
            with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(err):
                rc=shadow.main(["--config",str(path),"--allow-read"])
            self.assertEqual(rc,3)
            self.assertEqual(stdout.getvalue(),"")
            self.assertNotIn(str(path),err.getvalue())
            self.assertNotIn("abc",err.getvalue())

    def test_no_onnx_import_or_network_or_process_execution(self):
        code=SOURCE.read_text()
        for unsafe in ("import onnxruntime","subprocess.run(","os.system(",
                       "requests.get(","urllib.request","socket.connect(","eval(","exec("):
            self.assertNotIn(unsafe,code)


if __name__=="__main__":
    unittest.main()
