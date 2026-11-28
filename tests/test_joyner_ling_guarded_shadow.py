"""Negative controls for transient, NAS-audited Ling authentication shadow."""

import hashlib
import importlib.util
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/joyner_ling_guarded_shadow.py"
spec = importlib.util.spec_from_file_location("joyner_ling_guarded_shadow", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture(name):
    for kind, txt, limit in m.FIXTURES:
        if name == kind:
            return {
                "model": m.ALIAS,
                "messages": [{"role": "user", "content": txt}],
                "max_tokens": limit,
                "temperature": 0,
                "seed": 7,
                "stream": False,
            }
    raise KeyError(name)


class LingShadowTests(unittest.TestCase):
    def test_only_synthetic_fixture_allowed(self):
        for name, _, _ in m.FIXTURES:
            self.assertEqual(m.validate_body(fixture(name)), fixture(name))
        for mutation in (
            lambda x: x.update(model="other"),
            lambda x: x.update(stream=True),
            lambda x: x.update(max_tokens=4096),
            lambda x: x.update(temperature=0.5),
            lambda x: x.update(seed=19),
            lambda x: x.update(backend="http://other"),
            lambda x: x.update(messages=[{"role": "system", "content": "hi"}]),
        ):
            req = fixture("literal")
            mutation(req)
            with self.assertRaises(ValueError):
                m.validate_body(req)

    def test_large_invalid_request_denied(self):
        with self.assertRaises(ValueError):
            m.read_json_bytes(b"x" * (m.MAX_BODY + 1))
        with self.assertRaises(ValueError):
            m.read_json_bytes(b"null")
        with self.assertRaises(ValueError):
            m.read_json_bytes(b"[]")

    def test_strict_quality_evaluation(self):
        def payload(s):
            return {"choices": [{"message": {"content": s}}]}

        self.assertTrue(m.quality("literal", payload("READY")))
        self.assertFalse(m.quality("literal", payload("Ready")))
        self.assertTrue(m.quality("json", payload('{"ready": true}')))
        self.assertFalse(m.quality("json", payload('{"ready": false}')))
        self.assertFalse(m.quality("literal", {"choices": []}))

    def test_response_redaction_is_real(self):
        response = {
            "model": "joyner-ling",
            "choices": [{"message": {"content": "private-text-secret"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        data = m.response_metadata(response)
        self.assertEqual(data["text_sha256"], hashlib.sha256(b"private-text-secret").hexdigest())
        self.assertNotIn("private-text-secret", json.dumps(data))
        self.assertTrue(data["text_not_stored"])

    def test_wrong_host_is_denied(self):
        with self.assertRaises(PermissionError):
            m.check_host(hostname="not-joyner")

    def test_ephemeral_proxy_records_denials_and_positive_requests(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            ledger = m.Audit(Path(directory))
            key = "unit-test-token-not-for-real-fleet"
            called = []

            def backend(req):
                called.append(req)
                return {
                    "model": "legacy-model-internal-path",
                    "choices": [{"message": {"content": "READY"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 1},
                }

            srv = m.ShadowHTTP(ledger, key, backend=backend)
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            try:
                status, _ = m.issue(srv.server_port, "GET", "/v1/models")
                self.assertEqual(status, 401)
                status, catalog = m.issue(srv.server_port, "GET", "/v1/models", key)
                self.assertEqual(status, 200)
                self.assertEqual(catalog["data"][0]["id"], m.ALIAS)
                status, _ = m.issue(
                    srv.server_port, "POST", "/v1/chat/completions", None, fixture("literal")
                )
                self.assertEqual(status, 401)
                wrong = fixture("literal")
                wrong["model"] = "wrong-model"
                status, _ = m.issue(srv.server_port, "POST", "/v1/chat/completions", key, wrong)
                self.assertEqual(status, 400)
                status, response = m.issue(
                    srv.server_port, "POST", "/v1/chat/completions", key, fixture("literal")
                )
                self.assertEqual(status, 200)
                self.assertEqual(response["choices"][0]["message"]["content"], "READY")
                self.assertEqual(len(called), 1)
                hash_before = ledger.close()
                raw = ledger.path.read_bytes()
                self.assertEqual(hash_before, hashlib.sha256(raw).hexdigest())
                audit_json = json.loads(raw)
                self.assertEqual(len(audit_json["requests"]), 5)
                self.assertTrue(all(x["state"] == "FINISHED" for x in audit_json["requests"]))
                self.assertEqual(
                    [x["http_code"] for x in audit_json["requests"]], [401, 200, 401, 400, 200]
                )
                self.assertFalse(audit_json["cutover_authorized"])
                self.assertNotIn("unit-test-token-not-for-real-fleet", raw.decode())
                self.assertNotIn("READY", json.dumps([x["result"] for x in audit_json["requests"]]))
                self.assertTrue(all(x["content_saved"] is False for x in audit_json["requests"]))
            finally:
                srv.shutdown()
                srv.server_close()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def test_no_backend_dispatch_if_nas_pending_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            audit = m.Audit(Path(folder))
            called = []
            srv = m.ShadowHTTP(audit, "test-key", backend=lambda data: called.append(data))
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(audit, "pending", side_effect=OSError("NAS write")):
                    status, _ = m.issue(
                        srv.server_port,
                        "POST",
                        "/v1/chat/completions",
                        "test-key",
                        fixture("literal"),
                    )
                self.assertEqual(status, 503)
                self.assertEqual(called, [])
            finally:
                srv.shutdown()
                srv.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()


class BackendIdentityTests(unittest.TestCase):
    def _fake_proc(self, folder, model, uid=1001, alias="joyner-ling"):
        root = Path(folder)
        process = root / "1400830"
        process.mkdir(parents=True)
        (process / "cmdline").write_bytes(
            b"\0".join(
                x.encode()
                for x in (
                    "llama-server",
                    "--port",
                    "1236",
                    "-m",
                    str(model),
                    "--host",
                    "127.0.0.1",
                    "--alias",
                    alias,
                )
            )
            + b"\0"
        )
        (process / "status").write_text(f"Name:\tllama-server\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        return root

    def test_exact_model_process_sha(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "fixture.gguf"
            model.write_bytes(b"fixture for read-only exact gguf witness")
            proc = root / "proc"
            self._fake_proc(proc, model)
            with (
                patch.object(m, "LING_MODEL", model),
                patch.object(m, "LING_MODEL_BYTES", model.stat().st_size),
                patch.object(
                    m, "LING_MODEL_SHA256", hashlib.sha256(model.read_bytes()).hexdigest()
                ),
            ):
                data = m.verify_ling_backend(proc)
                self.assertEqual(data["verified_owner"], "scott")
                self.assertEqual(data["verified_process_count"], 1)
                self.assertEqual(data["backend_port"], 1236)
                model.write_bytes(b"X" * model.stat().st_size)
                with self.assertRaises(PermissionError):
                    m.verify_ling_backend(proc)

    def test_owner_or_alias_drift_denied(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "fixture.gguf"
            model.write_bytes(b"test")
            with (
                patch.object(m, "LING_MODEL", model),
                patch.object(m, "LING_MODEL_BYTES", model.stat().st_size),
                patch.object(
                    m, "LING_MODEL_SHA256", hashlib.sha256(model.read_bytes()).hexdigest()
                ),
            ):
                for uid, alias in ((999, "joyner-ling"), (1001, "surprise-ling")):
                    proc = root / f"proc-{uid}-{alias}"
                    self._fake_proc(proc, model, uid=uid, alias=alias)
                    with self.assertRaises(PermissionError):
                        m.verify_ling_backend(proc)

    def test_zero_process_denied(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                m.verify_ling_backend(Path(tmp))


class BackendFailureAuditTests(unittest.TestCase):
    def test_backend_exception_logged_as_502_without_model_text(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = m.Audit(Path(tmp))

            def failed_backend(data):
                raise TimeoutError("timeout")

            srv = m.ShadowHTTP(ledger, "fixture-key", backend=failed_backend)
            worker = threading.Thread(target=srv.serve_forever, daemon=True)
            worker.start()
            try:
                status, out = m.issue(
                    srv.server_port,
                    "POST",
                    "/v1/chat/completions",
                    "fixture-key",
                    fixture("literal"),
                )
                self.assertEqual(status, 502)
                self.assertEqual(out["error"], "upstream_unavailable")
                ledger.close()
                receipt = json.loads(ledger.path.read_text())
                self.assertEqual(len(receipt["requests"]), 1)
                self.assertEqual(receipt["requests"][0]["http_code"], 502)
                self.assertEqual(receipt["requests"][0]["outcome_class"], "backend_failure")
                self.assertEqual(receipt["requests"][0]["state"], "FINISHED")
                self.assertNotIn("timeout", ledger.path.read_text())
            finally:
                srv.shutdown()
                srv.server_close()
                worker.join(timeout=4)
