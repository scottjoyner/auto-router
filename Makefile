.PHONY: install dev test lint format docker-up docker-down

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

docker-up:
	docker compose up --build

docker-down:
	docker compose down
