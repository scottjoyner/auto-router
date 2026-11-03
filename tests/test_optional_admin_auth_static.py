"""Static fail-closed guard: admin routes must explicitly depend on require_admin.

Optional registrars are not mounted in the running secure_live application, but
may be enabled in a future release. This is source protection, not proof that
production is secured or a substitute for staged unauthenticated-route tests.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "auto_router"
MODULES = tuple(sorted(p.name for p in ROOT.glob("*.py")))


def _has_explicit_guard(fn, decorator):
    guard_sources = [fn.args] + [kw.value for kw in decorator.keywords if kw.arg == "dependencies"]
    for source in guard_sources:
        for node in ast.walk(source):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Depends"
                and any(
                    isinstance(arg, ast.Name) and arg.id == "require_admin" for arg in node.args
                )
            ):
                return True
    return False


class OptionalAdminRouteGuardTest(unittest.TestCase):
    def test_all_static_admin_handlers_are_explicitly_guarded(self):
        count = 0
        unguarded = []
        for module in MODULES:
            root = ast.parse((ROOT / module).read_text())
            for fn in ast.walk(root):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in fn.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    if not isinstance(decorator.func, ast.Attribute):
                        continue
                    if decorator.func.attr not in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "api_route",
                    }:
                        continue
                    if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                        continue
                    path = decorator.args[0].value
                    if not isinstance(path, str) or not path.startswith("/admin"):
                        continue
                    count += 1
                    if not _has_explicit_guard(fn, decorator):
                        unguarded.append((module, decorator.func.attr.upper(), path))
        self.assertGreaterEqual(count, 35, "Admin route audit unexpectedly narrowed")
        self.assertEqual([], unguarded)


if __name__ == "__main__":
    unittest.main()
