from __future__ import annotations

"""Strict-offline entrypoint with claim-scoped executor authentication.

Importing main_live constructs the reconciled router, removes retired execution
surfaces, and installs AssistX runtime-projection admission. This wrapper adds the
final data-plane boundary: every inference POST must carry a live AssistX task
token whose runtime generation, model, and token budgets match the request.
"""

from auto_router.executor_auth import install_executor_inference_auth
from auto_router.main_live import app, state

install_executor_inference_auth(app, state)

__all__ = ["app"]
