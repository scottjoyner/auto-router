"""Basic integration health tests — all 3 services must be running."""

from __future__ import annotations

import httpx


def test_assistx_health(assistx_client: httpx.Client) -> None:
    resp = assistx_client.get("/health")
    assert resp.is_success, f"AssistX health failed: {resp.status_code}"
    data = resp.json()
    assert data.get("service") in {"assistx", "auto-assist"}


def test_router_health(router_client: httpx.Client) -> None:
    resp = router_client.get("/health")
    assert resp.is_success, f"Router health failed: {resp.status_code}"
    data = resp.json()
    assert data.get("service") == "auto-router"


def test_router_api_health_alias(router_client: httpx.Client) -> None:
    resp = router_client.get("/api/health")
    assert resp.is_success, f"Router API health failed: {resp.status_code}"
    data = resp.json()
    assert data.get("service") == "auto-router"


def test_assign_health(assign_client: httpx.Client) -> None:
    resp = assign_client.get("/health")
    assert resp.is_success, f"Assign health failed: {resp.status_code}"
    data = resp.json()
    assert data.get("status") in ("ok", "degraded")


def test_correlation_id_propagation(assistx_client: httpx.Client) -> None:
    cid = "test-cid-integration"
    resp = assistx_client.get("/health", headers={"X-Correlation-ID": cid})
    assert resp.headers.get("X-Correlation-ID") == cid


def test_trace_id_propagation(assistx_client: httpx.Client) -> None:
    tid = "test-tid-integration"
    resp = assistx_client.get("/health", headers={"X-Trace-ID": tid})
    assert resp.headers.get("X-Trace-ID") == tid


def test_assistx_router_projection_surface(assistx_client: httpx.Client) -> None:
    resp = assistx_client.get("/api/router/context-projection")
    assert resp.is_success, f"Context projection failed: {resp.status_code}"

    projection = resp.json()
    metadata = projection.get("metadata", {})
    nodes = projection.get("nodes", [])

    assert projection.get("source") == "assistx"
    assert metadata.get("read_only") is True
    assert metadata.get("projection_version") == "router-context-v1"
    assert any(node.get("node_id") == "assistx-api" for node in nodes)

    backlog = assistx_client.get("/api/router/backlog-candidates?limit=5&queue=backlog&dry_run=true")
    assert backlog.is_success, f"Backlog candidates failed: {backlog.status_code}"

    backlog_payload = backlog.json()
    assert backlog_payload.get("read_only") is True
    assert backlog_payload.get("dry_run") is True
    assert isinstance(backlog_payload.get("tasks"), list)
