# Images for the whole repository, built from its root.
#   backend  the API and ingestion worker (Docker Compose runs one container of each)
#   app      the default: backend plus the built frontend, run as one service (Railway)

FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12-slim AS backend
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV PATH="/app/backend/.venv/bin:$PATH"
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --locked --no-dev
EXPOSE 8000
ENTRYPOINT ["sh", "/app/backend/scripts/start_api.sh"]

FROM node:24-alpine AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package.json
RUN npm ci --workspace frontend --include-workspace-root=false
COPY frontend/ frontend/
RUN npm --workspace frontend run build

FROM backend AS app
COPY --from=frontend /app/frontend/dist /app/frontend/dist
# Uploads live on the volume mounted at /data, shared by the API and worker processes.
ENV CONTEXTMESH_FRONTEND_DIR=/app/frontend/dist CONTEXTMESH_BLOB_DIR=/data/blobs
ENTRYPOINT ["bash", "/app/backend/scripts/start_app.sh"]
