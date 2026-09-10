.PHONY: api agent worker test lint architecture console gateway

api:
	uv run uvicorn apps.platform_api.main:app --reload --port 8000

agent:
	uv run uvicorn apps.agent_runtime.main:app --reload --port 8001

worker:
	uv run python -m apps.worker.main

test:
	uv run pytest -q

lint:
	uv run ruff check .

architecture:
	uv run pytest -q tests/architecture

console:
	cd frontend/console && pnpm dev

gateway:
	cd apps/channel_gateway && pnpm dev
