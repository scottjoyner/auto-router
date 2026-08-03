"""Strict-offline entrypoint with claim-scoped executor authentication.

Importing main_live constructs the reconciled router, removes retired execution
surfaces, and installs AssistX runtime-projection admission. This wrapper adds the
final data-plane boundaries: Ed25519 runtime projections, durable request
idempotency, iterator-final streaming accounting, and claim-scoped inference auth.
"""

from __future__ import annotations

import auto_router.main_live as main_live_module
from auto_router.executor_auth import install_executor_inference_auth
from auto_router.request_idempotency import install_request_idempotency
from auto_router.runtime_projection_v2 import RuntimeProjectionManager
from auto_router.stream_lifecycle import install_stream_lifecycle

# Replace the compatibility HMAC manager before the ASGI lifespan constructs any
# runtime state. The mounted production entrypoint therefore accepts only schema-v2
# Ed25519 projections while the legacy module remains available for migration tests.
main_live_module.RuntimeProjectionManager = RuntimeProjectionManager

app = main_live_module.app
state = main_live_module.state

# Starlette runs the last-added middleware outermost. Authentication is installed
# last so an invalid credential cannot reserve an idempotency row. The stream patch
# wraps the already-admitted main_live dispatch path and finalizes only when the
# iterator actually completes, fails, or is cancelled.
install_request_idempotency(app, state, main_live_module)
install_stream_lifecycle(main_live_module)
install_executor_inference_auth(app, state)

__all__ = ["app"]
