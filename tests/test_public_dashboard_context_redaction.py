"""Verify anonymous HTML never receives raw configured destination URLs.

Source-level contract, separate from protected admin JSON, with no live data.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from jinja2 import Environment, meta

ROOT = Path(__file__).resolve().parents[1] / "src" / "auto_router"
URL_KEYS = {"assistx_tasks_url", "assistx_event_sink_url"}
FRAGMENTS = ("dashboard_summary.html", "ops_summary.html")


def source_function(path: str, function: str):
    tree = ast.parse((ROOT / path).read_text())
    matches = [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == function]
    assert len(matches) == 1
    return matches[0]


def assert_redacted_dict(node):
    assert isinstance(node, ast.Dict)
    mapping = {k.value: v for k, v in zip(node.keys, node.values)
               if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert URL_KEYS <= set(mapping)
    for name in URL_KEYS:
        assert isinstance(mapping[name], ast.Constant)
        assert mapping[name].value is None


def test_main_public_fragment_context_omits_configured_urls():
    fn = source_function("main.py", "dashboard_summary")
    contexts = [n for n in ast.walk(fn) if isinstance(n, ast.keyword)
                and n.arg == "context" and isinstance(n.value, ast.Dict)]
    assert len(contexts) == 1
    assert_redacted_dict(contexts[0].value)


def test_ops_public_fragment_uses_private_copy_not_admin_json():
    fn = source_function("ops_dashboard_routes.py", "dashboard_ops_summary")
    assignments = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "data"
                           for t in n.targets)]
    assert len(assignments) == 2
    assert_redacted_dict(assignments[-1].value)
    admin = source_function("ops_dashboard_routes.py", "admin_ops_summary")
    returns = [n for n in ast.walk(admin) if isinstance(n, ast.Return)]
    assert len(returns) == 1
    assert isinstance(returns[0].value, ast.Call)
    assert isinstance(returns[0].value.func, ast.Name)
    assert returns[0].value.func.id == "build_ops_summary"


@pytest.mark.parametrize("fragment", FRAGMENTS)
def test_public_template_never_references_sensitive_configured_urls(fragment):
    path = ROOT / "templates" / "fragments" / fragment
    source = path.read_text()
    ast_template = Environment(autoescape=True).parse(source)
    variables = meta.find_undeclared_variables(ast_template)
    assert URL_KEYS.isdisjoint(variables)
    assert not any(name in source for name in URL_KEYS)
    assert "destination hidden" in source
