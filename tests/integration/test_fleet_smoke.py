"""Canonical end-to-end smoke coverage for the AssistX/router/assign stack."""

from __future__ import annotations

import httpx


def _find_provider(providers: list[dict[str, object]], *candidates: str) -> dict[str, object]:
    normalized = {candidate.strip().lower() for candidate in candidates}
    for provider in providers:
        name = str(provider.get("name") or "").strip().lower()
        node_id = str(provider.get("node_id") or "").strip().lower()
        if name in normalized or node_id in normalized:
            return provider
    raise AssertionError(f"none of the providers matched {candidates!r}")


def test_end_to_end_fleet_smoke(assistx_client: httpx.Client, router_client: httpx.Client, assign_client: httpx.Client) -> None:
    projection_resp = assistx_client.get("/api/router/context-projection")
    assert projection_resp.is_success, f"AssistX projection failed: {projection_resp.status_code}"
    projection = projection_resp.json()
    assert projection.get("source") == "assistx"
    assert projection.get("metadata", {}).get("read_only") is True
    assert any(node.get("node_id") == "assistx-api" for node in projection.get("nodes", []))

    backlog_resp = assistx_client.get("/api/router/backlog-candidates?limit=5&queue=backlog&dry_run=true")
    assert backlog_resp.is_success, f"AssistX backlog candidates failed: {backlog_resp.status_code}"
    backlog = backlog_resp.json()
    assert backlog.get("read_only") is True
    assert backlog.get("dry_run") is True
    assert isinstance(backlog.get("tasks"), list)

    heartbeat_resp = assign_client.get("/api/heartbeats/stale?limit=5&stale_after_seconds=1")
    assert heartbeat_resp.is_success, f"auto-assign stale heartbeat lookup failed: {heartbeat_resp.status_code}"
    heartbeat = heartbeat_resp.json()
    assert heartbeat.get("source") == "sqlite_cache_mirror"
    assert heartbeat.get("canonical_source") == "neo4j_via_assistx"
    assert heartbeat.get("stale_after_seconds") == 1
    assert isinstance(heartbeat.get("heartbeats"), list)

    health_resp = assign_client.get("/health")
    assert health_resp.is_success, f"auto-assign health failed: {health_resp.status_code}"
    health = health_resp.json()
    assert health["scheduler"]["control"]["mode"] == "enabled"
    assert health["scheduler"]["dispatch_enabled"] is True

    providers_resp = router_client.get("/admin/providers")
    assert providers_resp.is_success, f"router providers failed: {providers_resp.status_code}"
    providers_payload = providers_resp.json()
    providers = providers_payload.get("providers", [])
    macbook_air = _find_provider(
        providers,
        "lmstudio-scotts-macbook-air",
        "lmstudio-macbook-air",
        "scotts-macbook-air",
        "macbook-air-m2",
    )
    assert str(macbook_air.get("base_url") or "").endswith("scotts-macbook-air.tailcb8954.ts.net:1234/v1")
    models = macbook_air.get("models")
    assert isinstance(models, list)
    assert len(models) >= 1

    refresh_resp = router_client.post("/admin/live-models/refresh?provider=lmstudio-scotts-macbook-air")
    if refresh_resp.status_code == 404:
        refresh_resp = router_client.post("/admin/live-models/refresh?provider=lmstudio-macbook-air")
    assert refresh_resp.is_success, f"router live-model refresh failed: {refresh_resp.status_code}"
    refresh = refresh_resp.json()
    refreshed = next(
        (
            item
            for item in refresh.get("providers", [])
            if str(item.get("provider") or "").lower() in {"lmstudio-scotts-macbook-air", "lmstudio-macbook-air"}
        ),
        None,
    )
    assert refreshed is not None, "MacBook Air provider was not refreshed"
    assert refreshed.get("ok") is True
    refreshed_models = refreshed.get("models") or []
    live_models = {str(model.get("id") or "").lower() for model in refreshed_models}
    assert "refinedtoolcallv5-3b" in live_models
    assert "qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled" in live_models

    with httpx.Client(timeout=10.0, trust_env=False) as client:
        direct_resp = client.get("http://scotts-macbook-air.tailcb8954.ts.net:1234/api/v1/models")
    assert direct_resp.is_success, f"MacBook Air LM Studio probe failed: {direct_resp.status_code}"
    direct = direct_resp.json()
    direct_models = {str(model.get("key") or "").lower() for model in direct.get("models", [])}
    assert "refinedtoolcallv5-3b" in direct_models
    assert "qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled" in direct_models
