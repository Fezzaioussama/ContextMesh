# ContextMesh frontend

The React/TypeScript UI provides saved conversations with the ContextMesh Agent,
a **Sources** drawer for creating file or website sources (with live indexing
status), a per-message search-scope picker, and grounded answers with canonical
citations, evidence passages, gaps, and the agent's trace. Chat uses the streaming
HTTP endpoint to show progress and answer text before the saved turn arrives.
Direct responses stream during generation; sourced answers wait for verification
and publication checks before their text is released.

Answers render GitHub-flavored Markdown, including headings, lists, tables, and
fenced code. Model-supplied HTML stays inactive, remote images are not loaded, and
only controlled citation markers can navigate to evidence. Interrupted requests
keep their idempotency key and original source selection for an explicit retry.
The UI consumes only the backend HTTP API (see `/docs` on the running API).

Shared visual tokens and reduced-motion defaults live in `src/design-system.css`.
`src/brand.css` owns the wordmark and logo placement, while `src/styles.css`,
`src/knowledge.css`, and `src/message-content.css` style the workspace, knowledge,
and answer-reading experiences. The SVG browser icon is
`public/contextmesh-mark.svg`; its geometry matches the shared mesh icon.

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
for the combined frontend, API, worker, PostgreSQL, and Qdrant stack.

In production there is no Vite server: the root [Dockerfile](../Dockerfile) runs
`npm --workspace frontend run build`, and the API serves `dist/` at `/` on the same
origin as `/api` (see [Deploy to Railway](../docs/deploy-railway.md)).
