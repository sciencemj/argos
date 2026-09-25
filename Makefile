.PHONY: install dev migrate test lint format api-types

install:
	cd backend && uv sync
	cd frontend && bun install

migrate:
	cd backend && uv run alembic upgrade head

# Runs backend (:8000) and frontend (:5173) together; Ctrl-C stops both.
dev: migrate
	@trap 'kill $$(jobs -p) 2>/dev/null' EXIT INT TERM; \
	(cd backend && exec uv run uvicorn argos.main:app --reload --reload-dir src --host 127.0.0.1 --port 8000) & \
	(cd frontend && exec bun run dev) & \
	wait

test:
	cd backend && uv run pytest -q
	cd frontend && bun run test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright
	cd frontend && bun run lint && bun run typecheck

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .
	cd frontend && bun run format

# Regenerates frontend/src/api-types.ts from the FastAPI OpenAPI schema.
api-types:
	cd backend && uv run python -c "import json; from argos.main import app; print(json.dumps(app.openapi()))" > ../frontend/openapi.json
	cd frontend && bunx openapi-typescript openapi.json -o src/api-types.ts && rm openapi.json
