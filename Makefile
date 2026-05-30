.PHONY: install dev dev-base test lint format docker-up docker-down smoke

install:
	python -m pip install -e '.[dev]'

dev:
	uvicorn auto_router.main_live:app --host 0.0.0.0 --port 8088 --reload

dev-base:
	uvicorn auto_router.main:app --host 0.0.0.0 --port 8088 --reload

test:
	pytest -q

lint:
	ruff check src tests

format:
	ruff check --fix src tests

smoke:
	python -m py_compile $$(find src -name '*.py')
	pytest -q

docker-up:
	docker compose up --build

docker-down:
	docker compose down
