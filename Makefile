.PHONY: install dev test lint format smoke build docker-up docker-down

install:
	python -m pip install -e '.[dev]'

dev:
	uvicorn auto_router.main:app --host 0.0.0.0 --port 8088 --reload

test:
	pytest -q

lint:
	ruff check src tests

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

# Agentgateway sidecar targets (preserved)
.PHONY: gateway-up gateway-down gateway-smoke gateway-metrics jaeger-ui

gateway-up:
	docker compose -f docker-compose.yml -f docker-compose.agentgateway.yml up --build

gateway-down:
	docker compose -f docker-compose.yml -f docker-compose.agentgateway.yml down

gateway-smoke:
	@echo "Testing agentgateway health..."
	curl -s http://localhost:3000/ -H 'Content-Type: application/json' \
	  -d '{"model":"local/test","messages":[{"role":"user","content":"Say gateway online"}]}' | jq || echo "Gateway not ready"

gateway-metrics:
	@echo "Fetching agentgateway metrics..."
	curl -s http://localhost:15020/metrics | grep agentgateway_gen_ai || true

jaeger-ui:
	@echo "Jaeger UI available at http://localhost:16686"
