#!/usr/bin/env bash
# Blocking unit suite for the runtime mounted by auto_router.main_live.
#
# Excluded modules cover surfaces intentionally not registered by the reconciled
# entrypoint: external multi-service integration, in-process agent execution,
# router-owned backlog scheduling, and mutable provider/service discovery. They are
# preserved as migration history until their unique fixtures move to AssistX or are
# deleted with the retired code.
set -euo pipefail

pytest -q \
  --ignore=tests/integration \
  --ignore=tests/test_agent_jobs.py \
  --ignore=tests/test_backlog_routes.py \
  --ignore=tests/test_live_model_routes.py \
  --ignore=tests/test_live_models.py \
  --deselect=tests/test_config.py::test_project_live_models_merges_registry_snapshots_into_context \
  "$@"
