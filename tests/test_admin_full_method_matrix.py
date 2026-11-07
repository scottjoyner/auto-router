"""Exact loaded-app admin surface sweep, synthetic credentials only.

Runs without pytest via unittest, and under hosted pytest discovery.
NO network access, production secrets, admin mutations, or server sockets.
"""
from __future__ import annotations

import os
import re
import unittest

# Do not select production credentials as defaults. Staging/CI only.
os.environ.setdefault("AUTO_ROUTER_STRICT_OFFLINE", "true")
os.environ.setdefault("AUTO_ROUTER_FLEET_DISPATCHER_ENABLED", "false")
os.environ.setdefault("AUTO_ROUTER_DATABASE_URL", "sqlite:////tmp/router-admin-matrix-synthetic.sqlite3")
os.environ.setdefault("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-matrix-test-only")

from fastapi.testclient import TestClient  # noqa: E402

from auto_router.secure_live import app  # noqa: E402


class AdminMethodMatrix(unittest.TestCase):
    def registered_routes(self):
        methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        result = []
        for route in app.routes:
            path = getattr(route, "path", "")
            if isinstance(path, str) and (path == "/admin" or path.startswith("/admin/")):
                for method in sorted(methods.intersection(getattr(route, "methods", None) or ())):
                    target = re.sub(r"\{[^{}]+\}", "synthetic-id", path)
                    result.append((method, path, target))
        return result

    def test_admin_surface_all_method_paths_deny_anonymous(self):
        routes = self.registered_routes()
        self.assertGreaterEqual(len(routes), 19)
        names = {p for _, p, _ in routes}
        self.assertTrue(
            {"/admin/providers", "/admin/agent-jobs", "/admin/context",
             "/admin/load"}.issubset(names),
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            for method, path, target in routes:
                with self.subTest(method=method, path=path):
                    # Deliberately omit authentication and request body.
                    response = client.request(method, target)
                    self.assertEqual(response.status_code, 401)

    def test_authorized_provider_inventory_redacts_synthetic_token(self):
        token = os.environ.get("AUTO_ROUTER_ADMIN_TOKEN")
        # Never run with an unknown operator credential via this test.
        self.assertEqual(token, "synthetic-matrix-test-only")
        with TestClient(app, raise_server_exceptions=False) as client:
            good = client.get("/admin/providers", headers={"X-Admin-Token": token})
            bad = client.get("/admin/providers", headers={"X-Admin-Token": "wrong-synthetic"})
            empty = client.get("/admin/providers")
        self.assertEqual(good.status_code, 200)
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(empty.status_code, 401)
        self.assertEqual(good.headers.get("content-type", "").split(";")[0], "application/json")
        self.assertNotIn(token, good.text)

    def test_ambiguous_duplicate_admin_header_is_denied(self):
        token = os.environ.get("AUTO_ROUTER_ADMIN_TOKEN")
        self.assertEqual(token, "synthetic-matrix-test-only")
        with TestClient(app, raise_server_exceptions=False) as client:
            reply = client.get("/admin/providers", headers=[
                ("X-Admin-Token", token), ("X-Admin-Token", token),
            ])
        self.assertEqual(reply.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
