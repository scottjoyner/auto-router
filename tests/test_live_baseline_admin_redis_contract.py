"""Offline source-contract and isolated quota factory regressions for live-baseline fix."""
import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src" / "auto_router"

def tree(name):
    return ast.parse((SRC / name).read_text())

def isolated(tree, names, scope):
    nodes = [node for node in tree.body if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
        or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
    )]
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, "<isolated-source>", "exec"), scope)
    return scope

class LiveBaselineTests(unittest.TestCase):
    def test_admin_route_contract(self):
        routes = []
        for node in tree("main.py").body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str) and d.args[0].value.startswith("/admin/"):
                    routes.append(d.args[0].value)
                    dep = next(k.value for k in d.keywords if k.arg == "dependencies")
                    self.assertEqual(ast.unparse(dep), "[Depends(require_admin)]")
        self.assertEqual(len(routes), 13)
    def test_redaction(self):
        s = isolated(tree("main.py"), {"_ADMIN_PROVIDER_FIELDS", "_ADMIN_MODEL_FIELDS", "_redacted_admin_provider"}, {"Any": object})
        p = SimpleNamespace(model_dump=lambda: {"id":"x","headers":{"secret":"sentinel"},"base_url":"https://secret","api_key_env":"secret","models":[{"alias":"m","api_key":"secret"}]})
        self.assertEqual(s["_redacted_admin_provider"](p), {"id":"x","models":[{"alias":"m"}]})
    def factory(self, redis, impl):
        scope = {"REDIS_STARTUP_TIMEOUT_SECONDS": 10.0, "REDIS_STARTUP_INITIAL_BACKOFF_SECONDS": 0.2, "REDIS_STARTUP_MAX_BACKOFF_SECONDS": 2.0, "time": time, "redis_lib": redis, "InMemoryQuotaManager": lambda: "offline", "RedisQuotaManager": impl}
        return isolated(tree("quota.py"), {"build_quota_manager"}, scope)["build_quota_manager"]
    def test_offline(self):
        self.assertEqual(self.factory(None, None)(), "offline")
    def test_missing_client(self):
        with self.assertRaises(RuntimeError): self.factory(None, None)("redis://fixture")
    def test_failure(self):
        def bad(*a, **kw): raise OSError("synthetic")
        with self.assertRaises(RuntimeError): self.factory(object(), bad)("redis://fixture")
    def test_retry_parameters_preserved(self):
        f = self.factory(object(), lambda url, **kw: (url, kw))
        url, kwargs = f("redis://fixture", startup_retry_seconds=17, initial_backoff_seconds=0.4)
        self.assertEqual(url, "redis://fixture")
        self.assertEqual(kwargs["startup_retry_seconds"], 17)
        self.assertEqual(kwargs["initial_backoff_seconds"], 0.4)
    def test_health_requires_redis(self):
        health = next(n for n in tree("main.py").body if isinstance(n, ast.AsyncFunctionDef) and n.name == "health")
        checks = [ast.unparse(n) for n in ast.walk(health) if isinstance(n, ast.If)]
        self.assertTrue(any("_settings().redis_url" in s and 'state.quota_backend == \'RedisQuotaManager\'' in s and "redis_ok == 'ok'" in s for s in checks))

if __name__ == "__main__": unittest.main()
