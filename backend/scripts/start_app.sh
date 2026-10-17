#!/usr/bin/env bash
# One container runs the API and the ingestion worker, so both use the same upload
# volume (a Railway volume belongs to one service). When either process stops, the
# other is stopped too and the container exits, so the platform restarts both.
set -uo pipefail

alembic upgrade head || exit 1

python -m app.worker &
worker=$!
uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" &
api=$!

# A deploy or shutdown signal stops both processes instead of leaving one running.
trap 'kill -TERM "$api" "$worker" 2>/dev/null' TERM INT
wait -n "$api" "$worker"
status=$?
kill -TERM "$api" "$worker" 2>/dev/null
wait
exit "$status"
