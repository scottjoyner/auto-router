"""Source-level defense in depth for the exact reconciled Router release.

Does NOT replace a live HTTP test with missing/wrong/valid credentials. Avoids
importing the production ASGI app or connecting to production while auditing
all legacy admin route registrations.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "src" / "auto_router" / "main.py"
RUNTIME = ROOT / "src" / "auto_router" / "main_live.py"
SECURE = ROOT / "src" / "auto_router" / "secure_live.py"

REQUIRED = {
    "/admin/quota",
    "/admin/providers/health",
    "/admin/context",
    "/admin/context/graph",
    "/admin/providers",
    "/admin/providers/effective",
    "/admin/placement",
    "/admin/load",
    "/admin/agent-workers",
    "/admin/usage",
    "/admin/circuits",
    "/admin/circuits/reset",
    "/admin/circuits/reset/{owner:path}",
    "/admin/agent-jobs",
}

def _admin_functions():
    module = ast.parse(LEGACY.read_text())
    found = {}
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            first = decorator.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            route = first.value
            if not route.startswith("/admin/"):
                continue
            if route in found:
                raise AssertionError("duplicate inherited admin route: " + route)
            found[route] = node
    return found

def _uses_shared_admin_gate(fn):
    for element in ast.walk(fn.args):
        if not isinstance(element, ast.Call):
            continue
        if not isinstance(element.func, ast.Name) or element.func.id != "Depends":
            continue
        if element.args and isinstance(element.args[0], ast.Name):
            if element.args[0].id == "require_admin":
                return True
    return False

def test_all_inherited_admin_routes_fail_closed_by_dependency():
    functions = _admin_functions()
    assert REQUIRED <= set(functions), "required admin routes were removed or renamed"
    unguarded = sorted(route for route, fn in functions.items()
                      if not _uses_shared_admin_gate(fn))
    assert not unguarded, "legacy admin route lacks require_admin: " + repr(unguarded)

def test_shared_auth_import_and_strict_entrypoint_composition():
    main = ast.parse(LEGACY.read_text())
    imported = [alias.name
                for statement in main.body
                if isinstance(statement, ast.ImportFrom)
                and statement.module == "auto_router.security"
                for alias in statement.names]
    assert "require_admin" in imported
    runtime = RUNTIME.read_text()
    secure = SECURE.read_text()
    assert "auto_router.main" in runtime
    assert "app = main_live_module.app" in secure
    assert "install_executor_inference_auth" in secure

def test_admin_mutations_remain_authenticated():
    functions = _admin_functions()
    for route in ("/admin/load", "/admin/circuits/reset",
                  "/admin/circuits/reset/{owner:path}"):
        assert _uses_shared_admin_gate(functions[route])

def test_source_guard_no_token_fail_closed():
    source = (ROOT / "src" / "auto_router" / "security.py").read_text()
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "require_admin")
    # This guards the policy shape; a TestClient integration test must still
    # prove the actual 401/403 responses on an exact runtime image.
    assert any(isinstance(n, ast.If)
               and isinstance(n.test, ast.UnaryOp)
               and isinstance(n.test.op, ast.Not)
               and isinstance(n.test.operand, ast.Name)
               and n.test.operand.id == "token"
               for n in ast.walk(fn))
