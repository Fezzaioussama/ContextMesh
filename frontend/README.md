# ContextMesh frontend

The React/TypeScript UI provides saved conversations with the Foundation
Assistant. It consumes the HTTP contract in
[initial-chat.md](../docs/initial-chat.md); document retrieval remains planned.

Install Node.js 24+ and run these commands from the repository root:

```bash
npm ci
npm --workspace frontend run dev
npm --workspace frontend run typecheck
npm --workspace frontend run test
npm --workspace frontend run build
```

Start the API separately using the [root setup guide](../README.md). Vite serves
the UI at `http://127.0.0.1:5173` and proxies `/api` and `/health` to
`CONTEXTMESH_API_PROXY_TARGET`, defaulting to `http://127.0.0.1:8000`.

The repository owns one `.gitignore`, `.dockerignore`, `.env.example`, local
`.env`, `docker-compose.yml`, and npm `package-lock.json`. Copy the root
`.env.example` to `.env` when configuring local development. Vite reads the
proxy setting from that root environment; process environment overrides it.
Only variables with Vite's default `VITE_` prefix are exposed to browser code.
Provider credentials belong to the backend and must never use that prefix.

The frontend manifest owns React, TypeScript, Vite, and UI test dependencies.
Root ESLint checks frontend JavaScript/TypeScript; root Ruff checks Python.
Run `make quality` from the repository root for both gates.

The [frontend Dockerfile](Dockerfile) uses the root npm workspace manifest and
lockfile. Root Compose sets the proxy to the API service and runs Vite on
`0.0.0.0` inside the container, with a localhost published port. Use `make dev`
for the combined frontend/API/PostgreSQL development stack.
