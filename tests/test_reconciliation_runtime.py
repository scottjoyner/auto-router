from auto_router.main_live import app, strict_offline_enabled


def test_reconciled_entrypoint_is_strict_offline() -> None:
    assert strict_offline_enabled() is True


def test_reconciled_entrypoint_does_not_mount_duplicate_authority_routes() -> None:
    paths = {
        path
        for route in app.routes
        if (path := getattr(route, "path", None)) is not None
    }

    assert "/health" in paths
    assert "/v1/models" in paths
    assert "/api/routes/request" in paths

    assert "/admin/backlog/burn-down" not in paths
    assert "/admin/backlog/dry-run" not in paths
    assert "/admin/live-models" not in paths
    assert "/admin/live-models/refresh" not in paths
    assert "/admin/services" not in paths
    assert "/admin/services/scan" not in paths
    assert "/admin/agent-clis" not in paths
    assert "/admin/agent-clis/discover" not in paths
    assert "/jobs/agent" not in paths
