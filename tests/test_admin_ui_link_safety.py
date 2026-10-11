"""Dashboard link hygiene: API admin paths are references, not login bypasses."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from auto_router.ui_pages import get_ui_page_sections

TPL_ROOT = Path(__file__).resolve().parents[1] / "src/auto_router/templates"
ENV = Environment(loader=FileSystemLoader(str(TPL_ROOT)), autoescape=select_autoescape(["html"]))


def _render_section_loop(template: str) -> str:
    """Exercise only the page-shortcut component, not unrelated live telemetry."""
    source = (TPL_ROOT / template).read_text()
    start = source.index("{% for section in page_sections %}")
    # Exactly two nested section/link loops with an internal auth conditional.
    first_end = source.index("{% endfor %}", start)
    second_end = source.index("{% endfor %}", first_end + 12)
    fragment = source[start : second_end + len("{% endfor %}")]
    return ENV.from_string(fragment).render(page_sections=get_ui_page_sections())


def test_admin_catalog_links_are_identified_and_browser_pages_remain_clickable():
    sections = get_ui_page_sections()
    protected = [link for section in sections for link in section["links"] if link["href"].startswith("/admin/")]
    public = [link for section in sections for link in section["links"] if not link["href"].startswith("/admin/")]
    assert len(protected) >= 10 and public
    assert all(link["requires_admin_header"] is True for link in protected)
    assert all(link["requires_admin_header"] is False for link in public)
    assert get_ui_page_sections() is not sections


def test_no_hardcoded_admin_link_is_clickable_in_any_html_template():
    for template in TPL_ROOT.rglob("*.html"):
        source = template.read_text()
        assert not re.search(r'<a\b[^>]*href="/admin/', source, flags=re.S), template
        # Jinja templates must compile; browser auth must never be inserted via
        # query-string token or javascript into an HTML href.
        ENV.get_template(str(template.relative_to(TPL_ROOT)))


def test_main_dashboard_shortcuts_render_privileged_paths_as_text():
    html = _render_section_loop("fragments/dashboard_summary.html")
    assert 'href="/dashboard"' in html
    assert 'data-admin-api-path="/admin/providers"' in html
    assert "X-Admin-Token required" in html
    assert not re.search(r'<a\b[^>]*href="/admin/', html, flags=re.S)


def test_ops_shortcuts_keep_public_links_only():
    html = _render_section_loop("fragments/ops_summary.html")
    assert 'href="/dashboard"' in html
    assert 'data-admin-api-path="/admin/agent-jobs"' in html
    assert "API auth required" in html
    assert not re.search(r'<a\b[^>]*href="/admin/', html, flags=re.S)


def test_privileged_path_visibility_grants_no_authority():
    html = _render_section_loop("fragments/dashboard_summary.html")
    for forbidden in ("Bearer ", "?token=", "?api_key=", "?admin_token=", 'href="/admin/'):
        assert forbidden not in html


def test_no_browser_htmx_actions_target_any_admin_route():
    """A browser cannot safely attach the shared router administrator header.

    Keep operator HTTP actions in a separately authenticated service/client,
    not in HTMX, inline JavaScript, query parameters or unreviewed browser JS.
    """
    for template in TPL_ROOT.rglob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert not re.search(
            r'\\bhx-(?:get|post|put|patch|delete|trigger)\\s*=\\s*["\\\']\\s*/admin/',
            source, flags=re.I,
        ), template


def test_previously_live_admin_actions_remain_visible_as_nonclickable_references():
    expected = {
        "dashboard.html": [
            "/admin/providers/probe",
            "/admin/services/scan",
            "/admin/agent-clis/discover",
            "/admin/outbox/dispatch?dry_run=true&limit=10",
        ],
        "fragments/ops_summary.html": [
            "/admin/providers/probe",
            "/admin/agent-clis/discover",
            "/admin/outbox/dispatch?dry_run=true&limit=10",
        ],
    }
    for template, paths in expected.items():
        source = (TPL_ROOT / template).read_text(encoding="utf-8")
        for path in paths:
            assert f'data-admin-api-path="{path}"' in source
        assert "API auth required" in source
        assert 'X-Admin-Token' in source
        assert not re.search(r'<button\\b[^>]*hx-(?:post|get)="/admin/', source, flags=re.I | re.S)


def test_public_dashboard_templates_redact_raw_runtime_service_destinations():
    """Keep private URL values out of public HTML, even if a future URL adds
    embedded user-info or secret-looking query/path components.
    """
    secret_url = "http://private-host.example:8088/api?SYNTHETIC_BROWSER_SECRET=never"
    context = {
        "assistx_tasks_url": secret_url,
        "assistx_event_sink_url": secret_url,
        "assistx_tasks_configured": True,
        "assistx_event_sink_configured": True,
        "gateway": SimpleNamespace(base_url=secret_url, openai_base_url=secret_url),
    }
    target_files = (
        "fragments/dashboard_summary.html",
        "fragments/ops_summary.html",
    )
    watched_names = (
        "assistx_tasks_url", "assistx_event_sink_url",
        "gateway.base_url", "gateway.openai_base_url",
    )
    counts = 0
    for template in target_files:
        source = (TPL_ROOT / template).read_text(encoding="utf-8")
        for expression in re.findall(r"\\{\\{([\\s\\S]*?)\\}\\}", source):
            if not any(name in expression for name in watched_names):
                continue
            counts += 1
            output = ENV.from_string("{{" + expression + "}}").render(**context)
            assert "SYNTHETIC_BROWSER_SECRET" not in output, template
            assert "private-host.example" not in output, template
            assert "configured" in output, (template, output)
    assert counts >= 6, "Expected all current public runtime destination displays"


def test_no_direct_jinja_output_of_sensitive_runtime_origin_values():
    for template in TPL_ROOT.rglob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert not re.search(
            r"\\{\\{\\s*(?:assistx_tasks_url|assistx_event_sink_url"
            r"|gateway\\.(?:base_url|openai_base_url))\\b", source,
        ), template
