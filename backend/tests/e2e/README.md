# Agentic retrieval smoke

`make smoke-setup` installs the pinned Playwright Chromium runtime; `make smoke`
runs `backend/tests/e2e/runner.py`. Install backend/frontend dependencies first
and provide a real PostgreSQL database through `CONTEXTMESH_TEST_DATABASE_URL`
and a running Qdrant through `CONTEXTMESH_TEST_QDRANT_URL` (default
`http://127.0.0.1:6333`).
The default database is `contextmesh_test` on localhost port 55432. Alembic
migrations run before the smoke. This suite never truncates tables and creates a
unique server-side development identity and workspace per run.

`make smoke SMOKE_PROVIDER=openrouter` exercises OpenRouter selection, model
configuration, saved assistant message fields, and OpenRouter-specific setup
guidance against the same controlled fixture. The default smoke selects OpenAI.
Both runs override provider settings so local credentials never cause paid calls.

The runner starts a loopback OpenAI-compatible fixture (Responses and Embeddings
APIs), a real FastAPI process, a real ingestion worker, and a real Vite process.
The fixture answers each structured agent task by its schema name. The actual SDK
adapters receive the fixture URL and credential through server environment settings. No browser routes are
mocked, and no paid provider calls occur. These checks establish deterministic
adapter integration, not live-model quality or account access.

The smoke verifies upload through the API and the Sources drawer, worker indexing
into Qdrant, cited answers whose evidence resolves, the four bounded agent calls
per turn, idempotent replay and conflicts, saved planning context, strict provider
request bounds, safe upstream errors, same-key retry without duplicate messages,
sidebar history, refresh and API-restart persistence, inert HTML from documents,
mobile layout, keyboard behavior, and missing-key API/UI behavior. Backend integration tests separately establish concurrent turn
exclusion, expired execution recovery/fencing, and requester/workspace isolation.

Runtime logs plus desktop/mobile screenshots remain in a printed temporary
directory outside the repository. A browser failure also writes `failure.png`.
The runner accepts `--provider openai|openrouter`. Ports can be changed with
`--api-port` and `--frontend-port`; the database URL can
be supplied with `--database-url`. The suite stops its API/frontend and fixture
processes after both success and failure.

On an unsupported Linux version, Playwright may require a supported fallback
build through `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64`. Chromium still
needs its operating-system libraries. Use Playwright's `install --with-deps`
command on supported CI hosts. A missing browser/runtime is unavailable
verification, never a successful smoke.
