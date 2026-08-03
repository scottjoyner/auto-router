.PHONY: install dev test test-legacy-audit lint format smoke build docker-up docker-down

install:
	python -m pip install -e '.[dev]'

dev:
	uvicorn auto_router.main_live:app --host 0.0.0.0 --port 8088 --reload

test:
	bash scripts/test_reconciliation.sh

# Non-blocking/manual audit of code paths intentionally not mounted by main_live.
test-legacy-audit:
	pytest -q || true

lint:
	ruff check src tests

lint-reconciliation:
	ruff check --ignore I001,E501,UP012,UP018 \
		src/auto_router/access_paths.py \
		src/auto_router/admission.py \
		src/auto_router/main_live.py \
		src/auto_router/models.py \
		src/auto_router/offline_guard.py \
		src/auto_router/route_events.py \
		src/auto_router/runtime_projection.py \
		src/auto_router/strict_assistx_routes.py \
		scripts/verify_reconciliation_network.py \
		tests/test_access_paths.py \
		tests/test_admission.py \
		tests/test_offline_guard.py \
		tests/test_reconciliation_config.py \
		tests/test_reconciliation_runtime.py \
		tests/test_runtime_projection.py \
		tests/test_runtime_telemetry.py \
		tests/test_strict_assistx_routes.py \
		tests/test_flash_start.py \
		tests/test_main_health.py

format:
	ruff check --fix src tests scripts/verify_reconciliation_network.py

smoke:
	python -m compileall src scripts/verify_reconciliation_network.py
	bash scripts/test_reconciliation.sh

build:
	docker compose build

docker-up:
	docker compose up --build

docker-down:
	docker compose down

# Side-by-side strict-offline reconciliation router.
RECON_COMPOSE = docker compose -f docker-compose.yml -f compose.reconciliation.yml

.PHONY: reconciliation-init reconciliation-render reconciliation-up \
	reconciliation-status reconciliation-network-verify reconciliation-verify \
	reconciliation-image-ids reconciliation-logs reconciliation-down

reconciliation-init:
	@mkdir -p data-reconciliation artifacts-reconciliation
	@chmod +x scripts/test_reconciliation.sh scripts/verify_reconciliation_network.py
	@echo "Set AUTO_ROUTER_ADMIN_TOKEN to a unique shadow value."
	@echo "Set the shared runtime projection HMAC secret before startup."
	@echo "Verify physical ownership with official 'lms ps --host' before approving a generation."

reconciliation-render:
	@mkdir -p artifacts-reconciliation
	@$(RECON_COMPOSE) config > artifacts-reconciliation/router-rendered.yaml
	@echo "Rendered artifacts-reconciliation/router-rendered.yaml"

reconciliation-up:
	@$(RECON_COMPOSE) up -d --build redis llm-router

reconciliation-status:
	@$(RECON_COMPOSE) ps
	@curl -fsS http://127.0.0.1:18088/health | jq
	@curl -fsS http://127.0.0.1:18088/v1/models | jq

reconciliation-network-verify:
	@python scripts/verify_reconciliation_network.py

reconciliation-image-ids:
	@$(RECON_COMPOSE) images -q | sed '/^[[:space:]]*$$/d' | sort -u

reconciliation-verify: lint-reconciliation test reconciliation-render reconciliation-status reconciliation-network-verify
	@! grep -Eiq 'api\.openrouter\.ai|api\.cerebras\.ai|api\.groq\.com|api\.x\.ai|api\.anthropic\.com' \
		artifacts-reconciliation/router-rendered.yaml
	@echo "Router reconciliation checks passed."

reconciliation-logs:
	@$(RECON_COMPOSE) logs --no-color --tail=300

reconciliation-down:
	@$(RECON_COMPOSE) down
	@echo "Stopped only auto-router-reconciliation; isolated state directories remain."

# Broker gateways are intentionally unavailable on the reconciliation branch.
# `gateway-down` remains useful for containment if an old sidecar is running.
.PHONY: gateway-up gateway-down gateway-smoke gateway-metrics jaeger-ui

gateway-up gateway-smoke gateway-metrics jaeger-ui:
	@echo "Broker/hosted gateway targets are forbidden on full-auto-reconciliation-20260730." >&2
	@exit 2

gateway-down:
	@docker compose -f docker-compose.yml -f docker-compose.agentgateway.yml down || true
