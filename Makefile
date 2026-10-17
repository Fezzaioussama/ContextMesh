PYTHON ?= .venv/bin/python
RUFF ?= .venv/bin/ruff

.PHONY: npm-setup quality-setup quality quality-python quality-js

# This virtual environment holds the linters, not app runtime deps.
.venv/bin/python:
	uv venv --python 3.12 .venv

npm-setup:
	npm ci

quality-setup: .venv/bin/python npm-setup
	uv pip install --python $(PYTHON) -r requirements-quality.txt

quality: quality-python quality-js

quality-python:
	$(RUFF) check --config ruff.toml --no-cache .
	$(RUFF) format --config ruff.toml --check --no-cache .

quality-js:
	npm run quality:js

.PHONY: app-setup dev stop db migrate backend-dev worker-dev frontend-dev app-test smoke-setup smoke eval

.env:
	cp .env.example .env

app-setup: .env npm-setup
	uv sync --project backend --locked

dev: .env
	docker compose --env-file .env up --build -d --wait

stop:
	docker compose down

# PostgreSQL holds canonical state; Qdrant holds the rebuildable vector projection.
db:
	docker compose up -d --wait postgres qdrant

migrate:
	cd backend && uv run --locked alembic upgrade head

backend-dev:
	uv run --project backend --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload

worker-dev:
	uv run --project backend --locked python -m app.worker

frontend-dev:
	npm --workspace frontend run dev

# Offline agent evaluation over the starter dataset; e.g. ARGS="--repeats 3 --tag pricing".
eval:
	uv run --project backend --locked python -m app.evaluate --dataset backend/evals/datasets/starter.jsonl $(ARGS)

app-test:
	uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
	npm --workspace frontend run test
	npm --workspace frontend run build

smoke-setup:
	uv run --project backend --locked --with playwright==1.58.0 python -m playwright install chromium

smoke:
	uv run --project backend --locked --with playwright==1.58.0 python backend/tests/e2e/runner.py
