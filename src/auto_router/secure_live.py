"""Strict-offline entrypoint with claim-scoped executor authentication.

Importing main_live constructs the reconciled router, removes retired execution
surfaces, and installs AssistX runtime-projection admission. This wrapper adds the
final data-plane boundary: every inference POST must carry a live AssistX task
token whose runtime generation, model, and token budgets match the request.
"""

from __future__ import annotations

import auto_router.main_live as main_live_module
from auto_router.executor_auth import install_executor_inference_auth
from auto_router.runtime_projection_v2 import RuntimeProjectionManager

# Replace the compatibility HMAC manager before the ASGI lifespan constructs any
# runtime state. The mounted production entrypoint therefore accepts only schema-v2
# Ed25519 projections while the legacy module remains available for migration tests.
main_live_module.RuntimeProjectionManager = RuntimeProjectionManager

app = main_live_module.app
state = main_live_module.state
install_executor_inference_auth(app, state)

__all__ = ["app"]
