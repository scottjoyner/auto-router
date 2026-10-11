"""Synthetic status-only verification for the router admin cutover checker."""
from __future__ import annotations

import importlib.util
import json
import unittest
import urllib.error
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/router_admin_cutover_readonly.py"
spec = importlib.util.spec_from_file_location("router_cutover_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(probe)

ORIGIN = "http://100.64.43.123:8088"
TOKEN = "synthetic-operator-proof-not-real"
EFFECTIVE = {
    "schema_version": 1,
    "kind": "EFFECTIVE_ROUTER_MEMORY_READ_ONLY",
    "state_source": "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE",
    "no_probes_or_dispatch": True,
    "may_modify_routes": False,
    "may_prove_model_loaded": False,
    "provider_count": 1,
    "providers": [{
        "node_id": "x1-370",
        "name": "synthetic-model-provider",
        "configured_models": [{"provider_model": "ornith-example"}],
    }],
}


class FakeResponse:
    def __init__(self, status, body=b""):
        self.status = status
        self.body = body
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        self.reads += 1
        return self.body[:size]


class FakeOpener:
    def __init__(self, *, anon_status=None, authorized=EFFECTIVE, auth_status=200):
        self.calls = []
        self.responses = []
        self.anon_status = anon_status or {}
        self.auth_status = auth_status
        self.authorized = authorized

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        assert req.get_method() == "GET"
        assert timeout == 4.0
        path = req.full_url.removeprefix(ORIGIN)
        if path == "/admin/providers/effective":
            # Public test fixtures only, do not expose token in response.
            assert req.get_header("X-admin-token") == TOKEN
            code = self.auth_status
            body = json.dumps(self.authorized).encode() if self.authorized is not None else b""
        else:
            assert req.get_header("X-admin-token") is None
            code, body = self.anon_status.get(path, 401), b"SECRET_BODY_NOT_TO_READ"
        if code in (401, 403, 404):
            raise urllib.error.HTTPError(req.full_url, code, "synthetic", {}, None)
        resp = FakeResponse(code, body)
        self.responses.append((path, resp))
        return resp


class RouterCutoverEvidence(unittest.TestCase):
    def test_pass_with_four_anonymous_denials_and_valid_auth_effective(self):
        fake = FakeOpener()
        result = probe.check_cutover(ORIGIN, admin_token=TOKEN, opener=fake)
        self.assertEqual(result["status"], "READ_ONLY_BOUNDARY_PASS")
        self.assertEqual(result["authenticated_effective_catalog"], "VALID_SANITIZED_EFFECTIVE_SNAPSHOT")
        self.assertEqual(len(fake.calls), 5)
        self.assertTrue(all(x["pass"] for x in result["anonymous_admin_routes"].values()))
        self.assertEqual([p for p, r in fake.responses if r.reads], ["/admin/providers/effective"])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn("ornith-example", json.dumps(result))
        self.assertIs(result["production_release_authorized"], False)
        self.assertIs(result["did_post_or_modify_state"], False)

    def test_current_unpatched_router_reports_hold(self):
        fake = FakeOpener(anon_status={
            "/admin/providers": 200,
            "/admin/circuits": 200,
            "/admin/agent-workers": 200,
        }, auth_status=404)
        result = probe.check_cutover(ORIGIN, admin_token=TOKEN, opener=fake)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["anonymous_admin_routes"]["/admin/providers"]["http"], 200)
        self.assertEqual(result["authenticated_effective_catalog"], "INVALID_OR_UNAVAILABLE")
        self.assertTrue(all(r.reads == 0 for p, r in fake.responses if p != "/admin/providers/effective"))

    def test_no_token_does_not_probe_authenticated_route(self):
        fake = FakeOpener()
        result = probe.check_cutover(ORIGIN, admin_token=" ", opener=fake)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["authenticated_effective_catalog"], "MISSING_OPERATOR_TOKEN")
        self.assertEqual(len(fake.calls), 4)

    def test_invalid_effective_schema_or_redirect_stays_hold(self):
        for payload, code in (
            ({**EFFECTIVE, "may_prove_model_loaded": True}, 200),
            ({"detail": "not authorized"}, 200),
            (None, 200),
            (EFFECTIVE, 302),
            (EFFECTIVE, 503),
        ):
            with self.subTest(code=code, payload=payload):
                fake = FakeOpener(authorized=payload, auth_status=code)
                result = probe.check_cutover(ORIGIN, admin_token=TOKEN, opener=fake)
                self.assertEqual(result["status"], "HOLD")
                self.assertEqual(result["authenticated_effective_catalog"], "INVALID_OR_UNAVAILABLE")

    def test_reject_credentialed_or_public_origins_before_network(self):
        values = (
            "https://example.com",
            "http://8.8.8.8:8088",
            "http://user:pass@100.64.43.123:8088",
            "http://100.64.43.123:8088/admin/providers",
            "http://100.64.43.123:8088?token=unsafe",
            "http://100.64.43.123:invalid",
            "file:///etc/passwd",
        )
        fake = FakeOpener()
        for value in values:
            with self.subTest(origin=value):
                with self.assertRaises(ValueError):
                    probe.check_cutover(value, admin_token=TOKEN, opener=fake)
        self.assertEqual(fake.calls, [])

    def test_allow_loopback_private_lan_and_explicit_tailnet_origin(self):
        for url in (
            "http://localhost:8088",
            "http://127.0.0.1:8088",
            "http://192.168.1.237:8088",
            "http://100.64.43.123:8088",
            "https://router.example.ts.net",
        ):
            with self.subTest(url=url):
                self.assertEqual(probe.checked_origin(url), url)

    def test_no_arbitrary_paths_or_response_size(self):
        with self.assertRaises(ValueError):
            probe._read(ORIGIN, "/admin/circuits/reset", token=TOKEN, opener=FakeOpener())
        body = json.dumps(EFFECTIVE).encode()
        self.assertTrue(probe._valid_effective_snapshot(body))
        self.assertFalse(probe._valid_effective_snapshot(b"bad JSON"))
        self.assertFalse(probe._valid_effective_snapshot(b'{"error": "not ready"}'))
        self.assertFalse(probe._valid_effective_snapshot(None))


if __name__ == "__main__":
    unittest.main()
