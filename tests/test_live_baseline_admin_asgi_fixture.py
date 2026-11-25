"""Offline ASGI dependency proof using exact candidate route decorators.

Handlers are intentionally synthetic: these tests never initialize the router,
call providers, touch credentials, run lifecycle hooks or mutate circuit state.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src" / "auto_router"

def source(name):
    return (SRC / name).read_text()

class RouteAuthFixtureTests(unittest.TestCase):
    def test_all_inherited_admin_routes(self):
        setting = SimpleNamespace(admin_token="fixture-operator-only")
        sec = source("security.py")
        old = "from auto_router.settings import get_settings"
        self.assertEqual(sec.count(old), 1)
        scope = {"get_settings": lambda: setting, "__name__": "synthetic_security"}
        exec(compile(sec.replace(old, ""), "<security-fixture>", "exec"), scope)
        app = FastAPI()
        routes = []
        calls = []
        async def handler():
            calls.append(1)
            return {"fixture": True}
        for node in ast.parse(source("main.py")).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                path = dec.args[0].value
                if not isinstance(path, str) or not path.startswith("/admin/"):
                    continue
                self.assertIsInstance(dec.func, ast.Attribute)
                method = dec.func.attr.upper()
                # Evaluate only the exact simple dependency decorator contract.
                dep = next(k.value for k in dec.keywords if k.arg == "dependencies")
                self.assertEqual(ast.unparse(dep), "[Depends(require_admin)]")
                app.add_api_route(path, handler, methods=[method], dependencies=[Depends(scope["require_admin"])])
                routes.append((method, path.replace("{owner:path}", "fixture/owner")))
        self.assertEqual(len(routes), 13)
        with TestClient(app) as client:
            for method, path in routes:
                for headers, expected in (
                    ({}, 401),
                    ({"X-Admin-Token": "wrong"}, 401),
                    ({"X-Admin-Token": setting.admin_token}, 200),
                    ({"Authorization": "Bearer " + setting.admin_token}, 200),
                ):
                    with self.subTest(method=method, path=path, expected=expected):
                        before = len(calls)
                        self.assertEqual(client.request(method, path, headers=headers).status_code, expected)
                        self.assertEqual(len(calls) - before, int(expected == 200))
            setting.admin_token = ""
            for method, path in routes:
                with self.subTest(unconfigured=path):
                    before = len(calls)
                    self.assertEqual(client.request(method, path, headers={"X-Admin-Token": "fixture-operator-only"}).status_code, 401)
                    self.assertEqual(len(calls), before)
        print("ASGI_FIXTURE_65_CASES_PASS")

if __name__ == "__main__":
    unittest.main()
