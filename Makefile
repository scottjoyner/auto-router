.PHONY: install dev test lint format smoke build docker-up docker-down

install:
	python -m pip install -e '.[dev]'

dev:
	uvicorn auto_router.main_live:app --host 0.0.0.0 --port 8088 --reload

test:
	pytest -q

lint:
	ruff check src tests

lint-reconciliation:
	ruff check \
		src/auto_router/main_live.py \
		src/auto_router/offline_guard.py \
		tests/test_offline_guard.py \
		tests/test_reconciliation_config.py

format:
	ruff check --fix src tests

smoke:
	python -m compileall src
	pytest -q

build:
	docker compose build

docker-up:
	docker compose up --build

docker-down:
	docker compose down

# Side-by-side strict-offline reconciliation router.
RECON_COMPOSE = docker compose -f docker-compose.yml -f compose.reconciliation.yml

.PHONY: reconciliation-init reconciliation-render reconciliation-up \
	reconciliation-status reconciliation-verify reconciliation-logs reconciliation-down

reconciliation-init:
	@mkdir -p data-reconciliation artifacts-reconciliation
	@echo "Set RECONCILIATION_MODEL_ID and verify its physical runtime with official 'lms ps --host' before startup."

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

reconciliation-verify: lint-reconciliation reconciliation-render reconciliation-status
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
