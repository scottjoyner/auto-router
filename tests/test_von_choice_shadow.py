"""Isolated Von HTTP adapter tests; fake loopback server does NOT load models."""
import contextlib
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
SPEC = importlib.util.spec_from_file_location(
    "von_choice_shadow", ROOT / "experiments" / "von_choice_shadow.py"
)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def sample(**kw):
    row = {
        "case_id": "test_1", "variant": "original", "task_type": "choice",
        "state": "Node A is healthy and Node B is unreachable.",
        "instructions": "Select the healthy admitted node.",
        "criteria": {"node_a": "Node A", "node_b": "Node B"},
        "gold": "node_a", "eligible_options": ["node_a"],
        "evidence_complete": True,
    }
    row.update(kw)
    return row


def answer(**kw):
    data = {"model": "von-1.3.0", "truncation": None,
            "answers": {"shadow_0": {"type": "choice", "choice": "node_a",
                                     "probabilities": {"node_a": .9, "node_b": .1},
                                     "confidence": .9}}}
    data.update(kw)
    return json.dumps(data).encode()


def write_case(path, *cases):
    path.write_text("".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8")


class StubVon(BaseHTTPRequestHandler):
    requested = []
    truncated = False
    bad_status = False
    service_id = "von-decision-server"

    def do_GET(self):
        content = json.dumps({"status": "ok", "service": self.service_id,
                              "engine": "von-1.3", "version": "1.3.7"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        self.__class__.requested.append((self.path, json.loads(self.rfile.read(
            int(self.headers["Content-Length"])))))
        code = 503 if self.bad_status else 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if self.truncated:
            self.send_header("X-Von-Truncated", "state")
        self.end_headers()
        self.wfile.write(answer())

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def server():
    StubVon.requested = []
    StubVon.truncated = StubVon.bad_status = False
    StubVon.service_id = "von-decision-server"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubVon)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=3)


class TestVonAdapter(unittest.TestCase):
    def test_fixture_validation(self):
        self.assertEqual(V.validate_case(sample())["case_id"], "test_1")

    def test_rejects_unexpected_raw_data(self):
        with self.assertRaisesRegex(ValueError, "approved case fields"):
            V.validate_case(sample(raw_prompt="do not record"))

    def test_rejects_sensitive_email(self):
        with self.assertRaisesRegex(ValueError, "sensitive"):
            V.validate_case(sample(state="Contact dev@example.com"))

    def test_rejects_sensitive_ip(self):
        with self.assertRaisesRegex(ValueError, "sensitive"):
            V.validate_case(sample(state="Endpoint 192.168.1.1"))

    def test_rejects_secret_token(self):
        with self.assertRaisesRegex(ValueError, "sensitive"):
            V.validate_case(sample(state="Authorization: Bearer private-value"))

    def test_missing_independent_authority_field_refused(self):
        raw = sample()
        del raw["eligible_options"]
        with self.assertRaisesRegex(ValueError, "approved case fields"):
            V.validate_case(raw)

    def test_unauthorized_gold_refused(self):
        with self.assertRaisesRegex(ValueError, "authorized"):
            V.validate_case(sample(gold="node_b"))

    def test_incomplete_evidence_cannot_authorize_option(self):
        with self.assertRaisesRegex(ValueError, "cannot authorize"):
            V.validate_case(sample(variant="missing_evidence", gold=None,
                                   evidence_complete=False, eligible_options=["node_a"]))

    def test_displayed_but_ineligible_choice_is_flagged(self):
        import typed_decision_shadow as E

        case = V.validate_case(sample())
        row = V.observation(
            case, model_id="von", revision="fake-rev", status="ok",
            choice="node_b", confidence=.96, duration=8.0,
        )
        self.assertEqual(row["eligible_options"], ["node_a"])
        result = E.score([row], "von")
        self.assertEqual(result["models"]["von"]["ineligible_outputs"], 1)
        self.assertEqual(result["models"]["von"]["unsafe_or_ungrounded_output_count"], 1)
        self.assertEqual(result["models"]["von"]["accuracy_on_answered"], 0)

    def test_permutation_cannot_change_authorized_choices(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.jsonl"
            write_case(p, sample(), sample(
                variant="permuted", eligible_options=["node_b"],
                criteria={"node_b": "Node B", "node_a": "Node A"},
            ))
            with self.assertRaisesRegex(ValueError, "permutation"):
                V.fixture(p)

    def test_rejects_invalid_gold(self):
        with self.assertRaisesRegex(ValueError, "gold"):
            V.validate_case(sample(gold="node_c"))

    def test_incomplete_can_have_null_label(self):
        V.validate_case(sample(gold=None, evidence_complete=False,
                               variant="missing_evidence", eligible_options=[]))

    def test_permutation_must_reorder(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.jsonl"
            write_case(p, sample(), sample(variant="permuted"))
            with self.assertRaisesRegex(ValueError, "reorder"):
                V.fixture(p)

    def test_permutation_with_real_reorder(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.jsonl"
            write_case(p, sample(), sample(variant="permuted", criteria={
                "node_b": "Node B", "node_a": "Node A"}))
            self.assertEqual(len(V.fixture(p)[0]), 2)

    def test_reject_url_escape(self):
        for url in ("http://localhost:8000", "http://192.168.1.2:8000",
                    "https://127.0.0.1:8000", "http://127.0.0.1:8000/redirect",
                    "http://evil.example:8000", "http://127.0.0.1:8000?token=abc",
                    "http://name@127.0.0.1:8000"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                V.loopback_endpoint(url)

    def test_explicit_loopback_only(self):
        self.assertEqual(V.loopback_endpoint("http://127.0.0.1:8327"),
                         "http://127.0.0.1:8327/v1/systemone")

    def test_response_ok(self):
        x = V.decode_response(answer(), {}, sample(), "von-1.3.0")
        self.assertEqual(x, ("node_a", .9))

    def test_wrong_model_refused(self):
        with self.assertRaisesRegex(ValueError, "model mismatch"):
            V.decode_response(answer(model="von-other"), {}, sample(), "von-1.3.0")

    def test_truncation_refused(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            V.decode_response(answer(), {"X-Von-Truncated": "state"}, sample(), "von-1.3.0")

    def test_incorrect_probabilities_refused(self):
        malformed = json.loads(answer())
        malformed["answers"]["shadow_0"]["probabilities"]["node_a"] = 7
        with self.assertRaisesRegex(ValueError, "probability"):
            V.decode_response(json.dumps(malformed).encode(), {}, sample(), "von-1.3.0")

    def test_local_stub_http_decision(self):
        with server() as url:
            result = V.request_von(sample(), V.loopback_endpoint(url), timeout=2,
                                   expected_model="von-1.3.0")
            self.assertEqual(result[:3], ("ok", "node_a", .9))
            self.assertEqual(len(StubVon.requested), 1)
            self.assertEqual(StubVon.requested[0][0], "/v1/systemone")
            self.assertEqual(StubVon.requested[0][1]["model"], "von-1.3.0")

    def test_service_identity_preflight_passes(self):
        with server() as url:
            V.verify_von_service(V.loopback_endpoint(url), timeout=2)
            self.assertEqual(StubVon.requested, [])

    def test_unrelated_listener_refused_before_post(self):
        with server() as url, tempfile.TemporaryDirectory(dir="/tmp") as d:
            StubVon.service_id = "assistx-api"
            p = Path(d) / "cases.jsonl"
            out = Path(d) / "observations.jsonl"
            write_case(p, sample())
            with patch.object(sys, "argv", ["prog", str(p), "--execute",
                                            "--ack-sanitized", "--ack-preloaded",
                                            "--url", url, "--out", str(out)]):
                self.assertEqual(V.main(), 2)
            self.assertFalse(out.exists())
            self.assertEqual(StubVon.requested, [])

    def test_execute_requires_explicit_url(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = Path(d) / "cases.jsonl"
            out = Path(d) / "observations.jsonl"
            write_case(p, sample())
            with (
                patch.object(
                    V, "verify_von_service", side_effect=AssertionError("should not call")
                ),
                patch.object(sys, "argv", ["prog", str(p), "--execute",
                                          "--ack-sanitized", "--ack-preloaded",
                                          "--out", str(out)]),
            ):
                self.assertEqual(V.main(), 2)
            self.assertFalse(out.exists())

    def test_http_truncation_not_counted_as_valid(self):
        with server() as url:
            StubVon.truncated = True
            result = V.request_von(sample(), V.loopback_endpoint(url), timeout=2,
                                   expected_model="von-1.3.0")
            self.assertEqual(result, ("error", None, None, None))

    def test_http_non_200_not_counted(self):
        with server() as url:
            StubVon.bad_status = True
            result = V.request_von(sample(), V.loopback_endpoint(url), timeout=2,
                                   expected_model="von-1.3.0")
            self.assertEqual(result, ("error", None, None, None))

    def test_dry_run_does_not_send_http(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.jsonl"
            write_case(p, sample())
            with patch.object(V, "request_von", side_effect=AssertionError("should not call")), \
                    patch.object(sys, "argv", ["prog", str(p)]):
                self.assertEqual(V.main(), 0)

    def test_execute_requires_all_acknowledgements(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.jsonl"
            write_case(p, sample())
            with patch.object(V, "request_von", side_effect=AssertionError("should not call")), \
                    patch.object(sys, "argv", ["prog", str(p), "--execute"]):
                self.assertEqual(V.main(), 2)

    def test_end_to_end_stub_writes_allowlisted_rows(self):
        with server() as url, tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = Path(d) / "cases.jsonl"
            out = Path(d) / "observations.jsonl"
            write_case(p, sample())
            with patch.object(sys, "argv", ["prog", str(p), "--execute",
                                            "--ack-sanitized", "--ack-preloaded",
                                            "--url", url, "--out", str(out)]):
                self.assertEqual(V.main(), 0)
            item = json.loads(out.read_text().strip())
            self.assertEqual(item["source_kind"], "model_observation")
            self.assertEqual(item["prediction"], "node_a")
            self.assertNotIn("state", item)
            self.assertNotIn("instructions", item)
            self.assertNotIn("criteria", item)
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_no_overwrite_of_evidence(self):
        with server() as url, tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = Path(d) / "cases.jsonl"
            out = Path(d) / "observations.jsonl"
            write_case(p, sample())
            out.write_text("sentinel")
            with patch.object(sys, "argv", ["prog", str(p), "--execute",
                                            "--ack-sanitized", "--ack-preloaded",
                                            "--url", url, "--out", str(out)]):
                self.assertEqual(V.main(), 2)
            self.assertEqual(out.read_text(), "sentinel")
            self.assertEqual(len(StubVon.requested), 0)

    def test_simulated_server_rows_are_consumable_by_offline_scorer(self):
        # This is a protocol fixture, NOT any actual Von weight execution.
        import typed_decision_shadow as E
        with server() as url, tempfile.TemporaryDirectory(dir="/tmp") as d:
            cases_path = Path(d) / "cases.jsonl"
            observations_path = Path(d) / "von.jsonl"
            combined_path = Path(d) / "paired.jsonl"
            case = sample()
            write_case(cases_path, case)
            with patch.object(sys, "argv", ["prog", str(cases_path), "--execute",
                                            "--ack-sanitized", "--ack-preloaded",
                                            "--url", url, "--out", str(observations_path)]):
                self.assertEqual(V.main(), 0)
            von_row = json.loads(observations_path.read_text())
            base_row = V.observation(case, model_id="baseline",
                                     revision="synthetic-policy-v1", status="ok",
                                     choice="node_a", confidence=.95, duration=.2)
            base_row["source_kind"] = "synthetic"
            write_case(combined_path, base_row, von_row)
            rows, _ = E.load(combined_path)
            metrics = E.score(rows, "baseline")
            self.assertEqual(metrics["comparisons_to_baseline"]["von"]["paired_valid"], 1)
            self.assertFalse(metrics["execution_attested"])
            self.assertFalse(metrics["promotion_approved"])

    def test_output_must_be_tmp(self):
        with self.assertRaisesRegex(ValueError, "under /tmp"):
            V.output_path(Path("/nas/somewhere/observations.jsonl"))


if __name__ == "__main__":
    unittest.main()
