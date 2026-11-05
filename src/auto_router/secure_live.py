"""Strict-offline entrypoint with claim-scoped executor authentication.

Importing main_live constructs the reconciled router, removes retired execution
surfaces, and installs AssistX runtime-projection admission. This wrapper adds the
final data-plane boundaries: Ed25519 runtime projections, durable request
idempotency, iterator-final streaming accounting, and claim-scoped inference auth.
"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute, iter_route_contexts
from starlette.routing import Mount, WebSocketRoute
from auto_router.security import require_admin

import auto_router.main_live as main_live_module
from auto_router.executor_auth import install_executor_inference_auth
from auto_router.request_idempotency import install_request_idempotency
from auto_router.runtime_projection_v2 import RuntimeProjectionManager
from auto_router.stream_lifecycle import install_stream_lifecycle

def assert_mounted_admin_guards(application) -> None:
    """Fail ASGI import when any effective admin route can evade auth.

    FastAPI >=0.137 preserves included routers as lazy _IncludedRouter entries
    without .path. Use the supported iter_route_contexts() effective view;
    never assume application.routes is a flat list of APIRoute objects.
    No HTTP request, lifespan, network access or handler invocation occurs.
    """
    contexts = list(iter_route_contexts(application.routes))
    if not 0 < len(contexts) <= 1024:
        raise RuntimeError("mounted effective route inventory unverifiable")

    def effective(context):
        leaf = context.route
        fallback = getattr(context, "starlette_route", None)
        path = getattr(context, "path", None) or getattr(fallback, "path", None)
        pattern = getattr(context, "path_regex", None)
        if pattern is None:
            pattern = getattr(fallback, "path_regex", None)
        if pattern is None:
            pattern = getattr(leaf, "path_regex", None)
        if not isinstance(path, str) or pattern is None:
            raise RuntimeError("unverifiable effective route identity")
        return leaf, path, pattern

    resolved = [effective(context) for context in contexts]
    admin_paths = set()
    admin_count = 0
    for _leaf, path, _ in resolved:
        if path == "/admin" or path.startswith("/admin/"):
            admin_count += 1
            # Expand path parameters without making network calls.
            admin_paths.add(re.sub(r"\{[^{}]+\}", "guard-probe", path))
    admin_paths.update({
        "/admin", "/admin/", "/admin/__guard_probe__",
        "/admin/__guard_probe__/nested",
    })
    # FastAPI's supported effective-route iterator does not consistently
    # expose the Mount node itself. Audit top-level ASGI mounts separately:
    # /admin/private may hide its own child routes and Mount('/') can
    # intercept the entire admin namespace before any guarded endpoint.
    for top in application.routes:
        if not isinstance(top, (Mount, WebSocketRoute)):
            continue
        path = getattr(top, "path", None)
        regex = getattr(top, "path_regex", None)
        if (not isinstance(path, str) or regex is None
                or path == "/admin" or path.startswith("/admin/")
                or any(regex.match(probe) for probe in admin_paths)):
            raise RuntimeError("unsupported mounted admin route type")
    if admin_count < 1:
        raise RuntimeError("mounted admin surface missing; cannot prove auth contract")
    for leaf, _, pattern in resolved:
        if not any(pattern.match(probe) for probe in admin_paths):
            continue
        if isinstance(leaf, (Mount, WebSocketRoute)) or not isinstance(leaf, APIRoute):
            raise RuntimeError("unsupported mounted admin route type")
        if not any(
            dependency.call is require_admin
            for dependency in leaf.dependant.dependencies
        ):
            raise RuntimeError("mounted admin endpoint lacks compiled admin authentication")

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
