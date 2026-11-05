"""Strict-offline entrypoint with claim-scoped executor authentication.

Importing main_live constructs the reconciled router, removes retired execution
surfaces, and installs AssistX runtime-projection admission. This wrapper adds the
final data-plane boundaries: Ed25519 runtime projections, durable request
idempotency, iterator-final streaming accounting, and claim-scoped inference auth.
"""

from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.routing import Mount, WebSocketRoute
from auto_router.security import require_admin

import auto_router.main_live as main_live_module
from auto_router.executor_auth import install_executor_inference_auth
from auto_router.request_idempotency import install_request_idempotency
from auto_router.runtime_projection_v2 import RuntimeProjectionManager
from auto_router.stream_lifecycle import install_stream_lifecycle

def assert_mounted_admin_guards(application) -> None:
    """Fail process import before serving any unguarded admin route.

    Inspect the mounted app, not source decorators: included routers and
    inherited routes are represented by compiled FastAPI dependencies.
    Reject raw Starlette routes or mounts under the admin prefix because they
    bypass this dependency mechanism. No HTTP requests, network or secrets.
    """
    admin_count = 0
    for route in application.routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str) or not (
            path == "/admin" or path.startswith("/admin/")
        ):
            continue
        admin_count += 1
        if isinstance(route, (Mount, WebSocketRoute)) or not isinstance(route, APIRoute):
            raise RuntimeError("unsupported mounted admin route type")
        dependencies = route.dependant.dependencies
        if not any(dependency.call is require_admin for dependency in dependencies):
            raise RuntimeError("mounted admin endpoint lacks compiled admin authentication")
    if admin_count < 1:
        raise RuntimeError("mounted admin surface missing; cannot prove auth contract")


# Replace the compatibility HMAC manager before the ASGI lifespan constructs any
# runtime state. The mounted production entrypoint therefore accepts only schema-v2
# Ed25519 projections while the legacy module remains available for migration tests.
main_live_module.RuntimeProjectionManager = RuntimeProjectionManager

app = main_live_module.app
state = main_live_module.state
router_module = main_live_module.main_module

# Starlette runs the last-added middleware outermost. Authentication is installed
# last so an invalid credential cannot reserve an idempotency row. The strict-live
# wrapper owns admission, but the route functions execute from the underlying main
# module; patch that implementation explicitly rather than relying on private names
# being re-exported by the wrapper module.
install_request_idempotency(app, state, router_module)
install_stream_lifecycle(router_module)
install_executor_inference_auth(app, state)
# The production secure_live entrypoint must not serve even a *new* or raw
# Starlette /admin endpoint unless its compiled dependency is the actual
# admin verifier. Source-only and CI-only audits are not production guards.
assert_mounted_admin_guards(app)

__all__ = ["app"]
