from __future__ import annotations

from copy import deepcopy
from typing import Any

_UI_PAGE_SECTIONS: list[dict[str, Any]] = [
    {
        "title": "Browser pages",
        "description": "High-signal surfaces you can open directly in a browser.",
        "links": [
            {"label": "Dashboard", "href": "/dashboard", "note": "main operator view"},
            {"label": "Ops summary", "href": "/api/dashboard/ops-summary", "note": "fast ops fragment"},
            {"label": "Summary fragment", "href": "/api/dashboard/summary", "note": "full dashboard fragment"},
        ],
    },
    {
        "title": "Health and inventory",
        "description": "The primary GET endpoints for checking the live router state.",
        "links": [
            {"label": "Health", "href": "/health", "note": "service + dependency health"},
            {"label": "Metrics", "href": "/metrics", "note": "router metrics"},
            {"label": "Quota", "href": "/admin/quota", "note": "quota snapshots"},
            {"label": "Providers", "href": "/admin/providers", "note": "enabled providers"},
            {"label": "Provider health", "href": "/admin/providers/health", "note": "durable probe history"},
            {"label": "Live models", "href": "/admin/live-models", "note": "registry snapshot"},
            {"label": "Services", "href": "/admin/services", "note": "registered services"},
            {"label": "Context", "href": "/admin/context", "note": "current context snapshot"},
            {"label": "Context graph", "href": "/admin/context/graph", "note": "graph projection"},
            {"label": "Agent workers", "href": "/admin/agent-workers", "note": "worker registry"},
            {"label": "Usage", "href": "/admin/usage", "note": "recent routing usage"},
            {"label": "Circuits", "href": "/admin/circuits", "note": "circuit breaker state"},
            {"label": "Agent jobs", "href": "/admin/agent-jobs", "note": "submitted job queue"},
        ],
    },
    {
        "title": "AssistX and backlog",
        "description": "The intake, queue, and outbox surfaces that keep router work flowing after a restart.",
        "links": [
            {"label": "AssistX backlog config", "href": "/admin/backlog/assistx/config", "note": "intake settings"},
            {"label": "Agent jobs", "href": "/admin/agent-jobs", "note": "submitted work"},
            {"label": "Outbox", "href": "/admin/outbox", "note": "event dispatch queue"},
            {"label": "Ops summary", "href": "/admin/ops/summary", "note": "JSON ops summary"},
            {"label": "Ops preflight", "href": "/admin/ops/preflight", "note": "preflight report"},
            {"label": "Metrics ops", "href": "/metrics/ops", "note": "ops metrics text"},
        ],
    },
    {
        "title": "Operations and control",
        "description": "Support surfaces for router inventory and model/provider controls.",
        "links": [
            {"label": "Agent CLIs", "href": "/admin/agent-clis", "note": "CLI discovery results"},
            {"label": "Models list", "href": "/v1/models", "note": "OpenAI-compatible model list"},
        ],
    },
]


def get_ui_page_sections() -> list[dict[str, Any]]:
    return deepcopy(_UI_PAGE_SECTIONS)
