"""Fail-closed source coverage for every administrative FastAPI endpoint.

This is an AST contract, not proof of a deployed ASGI authentication canary.
Importing the production application is intentionally avoided.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "auto_router"
MUTATION_ROUTES = {
    "/admin/backlog/dry-run",
    "/admin/backlog/burn-down",
    "/admin/agent-clis/discover",
    "/admin/live-models/refresh",
    "/admin/providers/probe",
    "/admin/outbox/{event_id}/delivered",
    "/admin/outbox/{event_id}/failed",
}
READ_ROUTES = {
    "/admin/backlog/assistx/config",
    "/admin/agent-clis",
    "/admin/live-models",
    "/admin/services",
    "/admin/outbox",
}

def dependency_present(tree: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "Depends"
        and n.args
        and isinstance(n.args[0], ast.Name)
        and n.args[0].id == "require_admin"
        for n in ast.walk(tree)
    )

def inventory(root: Path = ROOT) -> dict[tuple[str, str], dict]:
    found: dict[tuple[str, str], dict] = {}
    for source in sorted(root.glob("*.py")):
        tree = ast.parse(source.read_text(), filename=str(source))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for decorator in fn.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                route = decorator.args[0]
                if not isinstance(route, ast.Constant) or not isinstance(route.value, str):
                    continue
                if not route.value.startswith("/admin/"):
                    continue
                method = (
                    decorator.func.attr.upper()
                    if isinstance(decorator.func, ast.Attribute) else "UNKNOWN"
                )
                key = method, route.value
                assert key not in found, f"duplicate route registration: {key}"
                guarded = dependency_present(fn.args) or dependency_present(decorator)
                found[key] = {"guarded": guarded, "module": source.name, "line": fn.lineno}
    return found

def test_all_registered_admin_routes_require_admin():
    routes = inventory()
    assert len(routes) >= 34, "unexpected admin route disappearance"
    unguarded = {k: v["module"] for k, v in routes.items() if not v["guarded"]}
    assert not unguarded, f"unauthenticated administrative routes: {unguarded}"

def test_secondary_read_and_write_routes_are_present_and_guarded():
    routes = inventory()
    for path in READ_ROUTES:
        assert routes[("GET", path)]["guarded"], path
    for path in MUTATION_ROUTES:
        assert routes[("POST", path)]["guarded"], path

def test_all_secondary_modules_import_shared_guard_and_fastapi_dependency():
    for name in ("backlog_routes.py","cli_routes.py","live_model_routes.py","service_routes.py"):
        text=(ROOT/name).read_text()
        tree=ast.parse(text)
        imports={(n.module,a.name) for n in tree.body if isinstance(n,ast.ImportFrom)
                 for a in n.names}
        assert ("fastapi","Depends") in imports
        assert ("auto_router.security","require_admin") in imports

def test_added_unprotected_admin_get_is_caught(tmp_path):
    candidate=tmp_path/"example.py"
    candidate.write_text('@app.get("/admin/accidental")\nasync def handler():\n    return 1\n')
    assert inventory(tmp_path)[("GET","/admin/accidental")]["guarded"] is False

def test_added_unprotected_admin_mutation_is_caught(tmp_path):
    candidate=tmp_path/"example.py"
    candidate.write_text('@app.post("/admin/accidental-mutation")\nasync def handler():\n    return 1\n')
    assert inventory(tmp_path)[("POST","/admin/accidental-mutation")]["guarded"] is False
