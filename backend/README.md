# Backend maintenance guide

ContextMesh's backend uses the requested `app` package layout, with one canonical
PostgreSQL database and Alembic migration history. The implemented feature is
provider-backed chat. Documents, ingestion, retrieval and workers remain future
milestones.

```text
backend/
  app/
    main.py
    api/
      deps.py
      errors.py
      v1/router.py
      v1/endpoints/{chat,health}.py
    core/{config,security,logging,exceptions}.py
    schemas/chat.py
    services/
      {chat_service,assistant,health_service}.py
      ports/{conversations,models,health}.py
    ai/
      llm/{base,factory,openai}.py
      prompts/system.py
    db/
      {base,session,health}.py
      models/{user,conversation}.py
      repositories/
    domain/{models,validation,errors}.py
    bootstrap/{api,runtime,conversations,schema}.py
  tests/{unit,integration,e2e}/
  migrations/
  scripts/start_api.sh
  Dockerfile
  pyproject.toml
  uv.lock
```

`services` owns use cases and integration protocols. `api` and `schemas` own HTTP
validation/serialization. `ai` and `db` implement outbound integrations. Domain
values and services use the standard library and inward contracts; they never
import framework, ORM, SDK, or configuration types. Pure identity/errors in
`core/security.py` and `core/exceptions.py` follow domain rules. Settings and
logging have adapter responsibilities. `bootstrap` injects concrete dependencies,
assembles schema metadata, and closes resources it creates; injected providers
remain owned by the caller. `main.py` exposes only the public application factory.

The conversation facade owns metadata, creation, history, and sending. Separate
conversation, turn, model and health ports keep consumers focused. PostgreSQL
transactions stay in repositories; provider calls happen outside transactions.
`api/deps.py` binds dependencies for each app instance, and `ai/llm/factory.py`
constructs the selected Responses adapter from plain values. No global container
or hidden lookup is needed. OpenAI and OpenRouter share that compatible adapter.

Run from the repository root:

```bash
make app-setup
make db
make migrate
make backend-dev
```

The Uvicorn entry point is `app.main:create_app` with `--factory`. `make dev`
uses root `docker-compose.yml` with `backend/Dockerfile` and
`frontend/Dockerfile` to start the API, frontend and PostgreSQL. `make stop`
retains database storage. Existing tables,
routes and saved conversations require no reset for this refactor.

All commands use the repository `.env`, created from the root example only if
absent. Exported process variables take precedence. The root `.gitignore` keeps
local credentials and generated backend/frontend artifacts out of Git.

Verification:

```bash
make quality
make quality-test
CONTEXTMESH_TEST_DATABASE_URL=postgresql+psycopg://contextmesh:contextmesh@127.0.0.1:55432/contextmesh_test \
  uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
```

Use a dedicated test database. Integration fixtures apply migrations and write
workspace-scoped records; without the variable, PostgreSQL integration tests skip.
The quality gate checks this package and retains the old layout's boundaries.
Files stay at most 1,000 physical lines and functions at complexity 4. Independent
architecture review remains required alongside automated checks.

Add the requested document/user endpoints, embedding/RAG/agent/guardrail modules,
vector stores, loaders, chunking, worker tasks and seed/ingestion scripts only with
their assigned milestones. Application ports and composition remain explicit.
Preserve ingestion/query separation, canonical publication and current access
checks. This refactor does not implement local LLMs or replace Qdrant with pgvector.

See [architecture](../docs/architecture.md), [ADRs 012–013](../docs/decisions.md),
[assistant contracts](../docs/initial-chat.md), [engineering](../docs/engineering.md),
and [repository layout](../docs/repository-layout.md).
