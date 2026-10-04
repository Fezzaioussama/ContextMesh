# Initial assistant smoke

`make smoke-setup` installs the pinned Playwright Chromium runtime; `make smoke`
runs `backend/tests/e2e/runner.py`. Install backend/frontend dependencies first
and provide a real PostgreSQL database through `CONTEXTMESH_TEST_DATABASE_URL`.
The default database is `contextmesh_test` on localhost port 55432. Alembic
migrations run before the smoke. This suite never truncates tables and creates a
unique server-side development identity per run.

`make smoke SMOKE_PROVIDER=openrouter` exercises OpenRouter selection, model
configuration, saved assistant message fields, and OpenRouter-specific setup
guidance against the same controlled fixture. The default smoke selects OpenAI.
Both runs override provider settings so local credentials never cause paid calls.

The runner starts a loopback OpenAI Responses HTTP fixture, real FastAPI process,
and real Vite process. The actual OpenAI SDK adapter receives the fixture URL and
fixture credential through server environment settings. No browser routes are
mocked, and no paid provider calls occur. These checks establish deterministic
adapter integration, not live-model quality or account access.

The smoke verifies public idempotent replay and conflicts, saved model context,
provider request bounds, safe upstream errors, same-key retry without duplicate
messages, browser chat and sidebar history, refresh persistence, API restart
persistence, inert model HTML, mobile layout, keyboard behavior, and missing-key
API/UI behavior. Backend integration tests separately establish concurrent turn
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
