"""Strict-offline entrypoint with claim-scoped executor authentication.

Importing main_live constructs the reconciled router, removes retired execution
surfaces, and installs AssistX runtime-projection admission. This wrapper adds the
final data-plane boundaries: Ed25519 runtime projections, durable request
idempotency, iterator-final streaming accounting, and claim-scoped inference auth.
"""

from __future__ import annotations

import uvicorn

import auto_router.main_live as main_live_module
from auto_router.executor_auth import install_executor_inference_auth
from auto_router.request_idempotency import install_request_idempotency
from auto_router.runtime_projection_v2 import RuntimeProjectionManager
from auto_router.settings import get_settings
from auto_router.stream_lifecycle import install_stream_lifecycle

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


def run() -> None:
    """Console-script entrypoint.

    Deliberately the same app the Dockerfile `CMD` runs. The router previously
    shipped ``auto_router.main_live:run``, which omits claim-scoped executor
    authentication, durable request idempotency, iterator-final streaming
    accounting, and the schema-v2 Ed25519 runtime projection -- so installing the
    package and running ``auto-router`` produced a materially weaker posture than
    ``docker compose up`` on the same source tree.

    ``auto_router.main_live:app`` remains importable so the schema-v1 HMAC
    migration path stays testable; it is simply no longer the default.
    """

    settings = get_settings()
    uvicorn.run(
        "auto_router.secure_live:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


__all__ = ["app", "run"]
