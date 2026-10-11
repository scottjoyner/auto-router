"""Focused audit of anonymous dashboard URL disclosure boundaries.

No router lifespan, network, API credentials, real URLs or user data are used.
"""
from __future__ import annotations
import ast
import re
from pathlib import Path

import jinja2
from jinja2 import meta
import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "auto_router"
FRAGMENTS = (
    "templates/fragments/dashboard_summary.html",
    "templates/fragments/ops_summary.html",
)
URL_NAMES = ("assistx_tasks_url", "assistx_event_sink_url")
URL_MARKERS = (
    "https://synthetic-user:synthetic-password@private.invalid",
    "https://private.invalid/?token=SYNTHETIC_SECRET",
)


@pytest.mark.parametrize("filename", FRAGMENTS)
def test_public_template_never_imports_endpoint_variable(filename):
    content = (ROOT / filename).read_text()
    variables = meta.find_undeclared_variables(
        jinja2.Environment(autoescape=True).parse(content)
    )
    assert set(URL_NAMES).isdisjoint(variables)
    assert all(name not in content for name in URL_NAMES)
    assert "configured · destination hidden" in content


@pytest.mark.parametrize("url", URL_MARKERS)
@pytest.mark.parametrize("filename", FRAGMENTS)
def test_public_status_placeholder_ignores_synthetic_secret_url(url, filename):
    # Render the two *real* status expressions from each HTML fragment.
    # Full dashboard data is not needed and no real origin/secret is supplied.
    content = (ROOT / filename).read_text()
    expressions = re.findall(
        r"\{\{\s*'configured · destination hidden'.*?\}\}", content
    )
    assert len(expressions) == 2
    env = jinja2.Environment(autoescape=True)
    values = {
        "assistx_tasks_configured": True,
        "assistx_event_sink_configured": True,
        "assistx_tasks_url": url,
        "assistx_event_sink_url": url,
    }
    for expression in expressions:
        assert not any(name in expression for name in URL_NAMES)
        rendered = env.from_string(expression).render(**values)
        assert rendered == "configured · destination hidden"
        assert url not in rendered


def test_main_dashboard_context_explicitly_replaces_both_url_values_with_none():
    parsed = ast.parse((ROOT / "main.py").read_text())
    routes = [n for n in ast.walk(parsed) if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "dashboard_summary"]
    assert len(routes) == 1
    found = {}
    for node in ast.walk(routes[0]):
        if isinstance(node, ast.Dict):
            for k, value in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in URL_NAMES:
                    found[k.value] = value
    assert set(found) == set(URL_NAMES)
    assert all(isinstance(value, ast.Constant) and value.value is None
               for value in found.values())


def test_ops_fragment_uses_redacted_shallow_copy_not_admin_json_data():
    parsed = ast.parse((ROOT / "ops_dashboard_routes.py").read_text())
    functions = [n for n in ast.walk(parsed) if isinstance(n, ast.AsyncFunctionDef)
                 and n.name in {"dashboard_ops_summary", "admin_ops_summary"}]
    assert len(functions) == 2
    fragments = next(n for n in functions if n.name == "dashboard_ops_summary")
    administrative = next(n for n in functions if n.name == "admin_ops_summary")
    overwritten = set()
    for node in ast.walk(fragments):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            k.value: v for k,v in zip(node.keys,node.values)
            if isinstance(k, ast.Constant) and k.value in URL_NAMES
        }
        if keys and all(isinstance(v, ast.Constant) and v.value is None
                        for v in keys.values()):
            overwritten.update(keys)
    assert overwritten == set(URL_NAMES)
    # Separate privileged admin JSON endpoint keeps its previous handler,
    # avoiding an accidental operator contract change.
    assert "build_ops_summary" in ast.unparse(administrative)
    assert not any(name in ast.unparse(administrative) for name in URL_NAMES)


def test_no_changes_required_to_admin_route_auth_for_dashboard_only_fix():
    src = (ROOT / "ops_dashboard_routes.py").read_text()
    assert "@app.get(\"/admin/ops/summary\")" in src
    assert "Depends(require_admin)" in src
