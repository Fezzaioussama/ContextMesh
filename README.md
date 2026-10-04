# ContextMesh

**Agentic Knowledge Retrieval Across Any Source**

An extensible Agentic RAG platform for conversational search across documents, websites and enterprise data sources.

**Project status:** implementation has started. The first slice builds a React
frontend, FastAPI backend, and PostgreSQL-backed **Foundation Assistant**, using
a real model provider configured on the server. Document ingestion, grounded
retrieval, citations, and the ingestion worker remain planned. M1 is in progress.

Suggested GitHub repository name: `context-mesh`.

## Run the initial assistant

Install Docker with Compose, then run from the repository root:

```bash
make dev
```

This creates `.env` from [.env.example](.env.example) only if it does not already
exist, applies database migrations, and starts the local frontend, API, and
PostgreSQL. Open [http://localhost:5173](http://localhost:5173). API documentation
is at [http://localhost:8000/docs](http://localhost:8000/docs).

For OpenRouter, set these server-only values in `.env`, then run `make dev` again:

```dotenv
CONTEXTMESH_MODEL_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_MODEL=openai/gpt-4.1-mini
```

Choose another `OPENROUTER_MODEL` using its full OpenRouter model ID.
`OPENROUTER_BASE_URL` defaults to `https://openrouter.ai/api/v1`. OpenRouter's
[stateless Responses API](https://openrouter.ai/docs/api_reference/responses/basic-usage)
uses saved conversation history without provider-side conversation storage.

For direct OpenAI access, set `CONTEXTMESH_MODEL_PROVIDER=openai`,
`OPENAI_API_KEY`, and an accessible `OPENAI_MODEL` in `.env`, then run `make dev`.
The default model is `gpt-4.1-mini`; `OPENAI_BASE_URL` optionally selects a
compatible Responses API endpoint. Each provider uses its own key and model.
Credentials stay on the server.
Without credentials the UI shows setup instructions; it does not invent replies.
The adapter uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/conversation-state).

The assistant supports saved conversations and bounded model replies. Its UI
labels this as provider chat with no connected knowledge retrieval. These replies
are general assistant output and carry no document citations or evidence claims.
The local development identity is not suitable for shared hosting.

`make stop` stops the containers while retaining PostgreSQL's named volume.
Only localhost ports are published: frontend `5173`, API `8000`, PostgreSQL
`55432`. A provider key is required for live replies.

For development with hot reload, install Python 3.12+, uv, and Node.js 24+:

```bash
make app-setup
make db
make migrate
make backend-dev
# In another terminal:
make frontend-dev
```

Backend code and its Dockerfile are in [backend](backend). The UI and its
Dockerfile are in [frontend](frontend). Shared Compose, environment, ignore,
workspace lock, and quality configuration live at the repository root. The shared
HTTP contract and this slice's limits
are documented in [docs/initial-chat.md](docs/initial-chat.md).

## Verify the implementation

```bash
make quality-setup
make quality
make quality-test
make app-test
make smoke-setup
make smoke
make smoke SMOKE_PROVIDER=openrouter
```

PostgreSQL integration and browser checks use a **separate disposable database**.
For the provided local PostgreSQL container, create it once:

```bash
docker compose exec -T postgres psql -U contextmesh -d postgres -c 'CREATE DATABASE contextmesh_test'
export CONTEXTMESH_TEST_DATABASE_URL='postgresql+psycopg://contextmesh:contextmesh@127.0.0.1:55432/contextmesh_test'
make app-test
make smoke
```

The browser smoke check runs the real frontend, API, PostgreSQL persistence, and
provider adapter against a controlled local Responses API fixture. It does not
contact a paid model. Live model testing requires separately configured credentials.
Application CI runs the same checks with its own PostgreSQL service.
See [the verification record](docs/initial-chat-verification.md) for observed
results and the distinction between fixture and live-provider verification.
OpenRouter selection and request compatibility are covered by the
[OpenRouter verification record](docs/openrouter-verification.md).

On Linux distributions not recognized by the pinned Playwright browser installer,
use `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 make smoke-setup` for the
Ubuntu-compatible Chromium build. The runtime still needs Chromium's system
libraries; CI installs them with Playwright's `--with-deps` option.

## The experience

Ask: **“What did we decide about the authentication architecture?”**

ContextMesh will select relevant sources, search for evidence, expand its search when evidence is missing, and return an answer with citations. Users will see which sources were searched and why another retrieval round was needed. If the indexed sources cannot support an answer, the system will explain the gap.

In the MVP, those sources are uploaded documents and explicitly added web pages. GitHub, Notion, Slack, and other enterprise connectors follow in later milestones.

## Proposed architecture

A modular monolith with Clean/Hexagonal boundaries, a FastAPI API, and a separate ingestion worker. Both processes share one Python package; ingestion and querying have independent execution paths.

```mermaid
flowchart LR
    Sources[Files and web pages] --> Worker[Ingestion worker]
    Worker --> Blob[Raw document storage]
    Worker --> PG[(PostgreSQL)]
    Worker --> Q[(Qdrant)]
    UI[React interface] --> API[FastAPI]
    API --> Jobs[Durable ingestion jobs]
    Jobs --> Worker
    Jobs -. stored in .-> PG
    API --> Agent[LangGraph retrieval workflow]
    Agent --> PG
    Agent --> Q
    Agent --> Model[Model provider]
    Agent --> Answer[Validated answer and citations]
    Answer --> UI
```

| Responsibility | Initial choice |
| --- | --- |
| UI | React, TypeScript, Vite |
| API and use cases | Python, FastAPI |
| Agent orchestration | LangGraph |
| Metadata, authorization, chat, jobs | PostgreSQL, SQLAlchemy, Alembic |
| Vector retrieval | Qdrant |
| Keyword retrieval | PostgreSQL full-text search |
| Complex document parsing | Unstructured behind a parser interface |
| Raw document storage | Local filesystem adapter; S3-compatible adapter later |
| Chat, embeddings, reranking | Replaceable provider interfaces |
| Development environment | Docker Compose, Python and JavaScript lockfiles |

This table describes the full target. The initial assistant's dependencies are
locked in `backend/uv.lock` and the root `package-lock.json`; model selection is
server configuration. Retrieval and ingestion integrations remain planned.

## First release scope

- Upload TXT and Markdown first, then text-bearing PDF, DOCX, and PPTX.
- Ingest individual public HTML pages supplied by the user.
- Track ingestion progress, errors, retries, document versions, and deletion.
- Combine vector and keyword results, rerank evidence, and build a bounded context.
- Support conversational questions with source filters, citations, and explicit insufficient-evidence responses.
- Add bounded source selection and search expansion after establishing a conventional RAG baseline.
- Include source ownership checks, retrieval isolation tests, and a reproducible evaluation dataset.

OCR, recursive crawling, enterprise OAuth, live SQL/API tools, and distributed microservices are later extensions.

## Design documents

| Document | Purpose |
| --- | --- |
| [Architecture](docs/architecture.md) | Runtime, module boundaries, data model, ingestion, and agent workflow |
| [Contracts](docs/contracts.md) | Connector and provider interfaces, durable jobs, API shapes, and citations |
| [Implementation roadmap](docs/roadmap.md) | Ordered milestones, dependencies, tasks, and acceptance criteria |
| [Architecture decisions](docs/decisions.md) | Proposed decisions, tradeoffs, and conditions for revisiting them |
| [Quality and evaluation](docs/quality.md) | Access control, failure cases, test strategy, and retrieval evaluation |
| [Implementation agents](docs/agents.md) | Six specialist roles, ownership, and independent architecture review |
| [Engineering rules](docs/engineering.md) | SOLID, pattern selection, automated dependency checks, file and complexity limits |
| [Repository layout](docs/repository-layout.md) | Shared configuration ownership, workspaces, Docker and developer commands |
| [Repository layout verification](docs/repository-layout-verification.md) | Observed consolidation checks and limitations |

Start with the architecture, then follow milestones **M1–M5** to reach the MVP. The first implementation milestone is a working development environment and a tested domain/persistence foundation.

## Development agents and checks

Project-scoped Codex roles in `.codex/agents/` cover backend, ingestion, retrieval, frontend, QA, and architecture review. All implementation roles follow SOLID and the documented architecture, with at most **1,000 physical lines per maintained file** and **cyclomatic complexity 4 per function/method**.

Run `make quality-setup`, `make quality`, and `make quality-test` from the repository root. The tooling validates these limits and selected import boundaries; the architecture guardian separately reviews design and behavior. See the [agent workflow](docs/agents.md) and [measurement policy](docs/engineering.md) for details. `make app-test` and `make smoke` check the initial assistant behavior separately.

## Portfolio demonstration

Use a synthetic corpus containing an authentication decision record, a platform handbook, and rollout notes. Demonstrate a direct question, a follow-up, an answer that requires a second source, a conflict between document versions, and an unanswerable question. Show citations and measured results against the baseline retrieval system.

The knowledge retrieval and portfolio demonstration features remain planned.
The initial assistant setup above is separate from those milestones; deterministic
application checks do not establish retrieval quality or live-model benchmarks.
