# ContextMesh

**Agentic Knowledge Retrieval Across Any Source**

An extensible Agentic RAG platform for conversational search across documents, websites and enterprise data sources.

**Project status:** the first agentic retrieval slice works end to end. Upload
Markdown or text files into sources; a separate worker parses, chunks, embeds, and
publishes them. The **ContextMesh Agent** plans which sources to search, runs
hybrid retrieval, expands or reformulates the search when evidence is missing, and
returns claims with verified citations — or an explicit evidence gap. Website and
PDF/Office ingestion, streaming, and the evaluation benchmark remain planned.

Suggested GitHub repository name: `context-mesh`.

## Run it

Install Docker with Compose, then run from the repository root:

```bash
make dev
```

This creates `.env` from [.env.example](.env.example) only if it does not already
exist, applies database migrations, and starts the frontend, API, ingestion worker,
PostgreSQL, and Qdrant. Open [http://localhost:5173](http://localhost:5173), choose
**Sources**, create a source, and upload `.md`, `.markdown`, or `.txt` files. When a
document shows **Ready**, ask a question. API documentation is at
[http://localhost:8000/docs](http://localhost:8000/docs).

The selected provider's key is used for both the reasoning model and embeddings.
For OpenRouter, set these server-only values in `.env`, then run `make dev` again:

```dotenv
CONTEXTMESH_MODEL_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_MODEL=openai/gpt-4.1-mini
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
CONTEXTMESH_MAX_OUTPUT_TOKENS=4096
```

Reasoning models (for example DeepSeek) spend hidden reasoning tokens inside the
output budget; keep `CONTEXTMESH_MAX_OUTPUT_TOKENS` at 4096 for them. If a reply
runs out of budget, the API returns `model_output_limit` rather than a partial
answer. For direct OpenAI access, set `CONTEXTMESH_MODEL_PROVIDER=openai`,
`OPENAI_API_KEY`, `OPENAI_MODEL`, and optionally `OPENAI_EMBEDDING_MODEL`
(default `text-embedding-3-small`). Each embedding model gets its own Qdrant
collection. After changing the embedding model, delete existing documents (or
their source) and upload them again: re-uploading unchanged files is idempotent
and does not re-index them, so until then those documents are found by keyword
search only. Credentials stay on
the server; without them the UI shows setup instructions and never invents replies.

`make stop` stops the containers and keeps the PostgreSQL, Qdrant, and upload
volumes. Only localhost ports are published: frontend `5173`, API `8000`,
PostgreSQL `55432`, Qdrant `6333`. The local development identity is not suitable
for shared hosting.

For development with hot reload, install Python 3.12+, uv, and Node.js 24+:

```bash
make app-setup
make db          # PostgreSQL and Qdrant
make migrate
make backend-dev
# In other terminals:
make worker-dev
make frontend-dev
```

Backend code and its Dockerfile are in [backend](backend); its README describes
the module layout, agent workflow, and data model. The UI is in [frontend](frontend).

## Verify the implementation

```bash
make app-test
make smoke-setup
make smoke
make smoke SMOKE_PROVIDER=openrouter
```

Integration and browser checks use a **separate disposable database** and a
running Qdrant. With the provided containers, create the database once:

```bash
docker compose exec -T postgres psql -U contextmesh -d postgres -c 'CREATE DATABASE contextmesh_test'
export CONTEXTMESH_TEST_DATABASE_URL='postgresql+psycopg://contextmesh:contextmesh@127.0.0.1:55432/contextmesh_test'
export CONTEXTMESH_TEST_QDRANT_URL='http://127.0.0.1:6333'
make app-test
make smoke
```

Backend integration tests run against real PostgreSQL with an in-memory Qdrant;
Qdrant server tests run when `CONTEXTMESH_TEST_QDRANT_URL` is set. The browser smoke
runs the real frontend, API, worker, PostgreSQL, and Qdrant against a controlled
local provider fixture (Responses and Embeddings APIs). It never contacts a paid
model, so it establishes integration behavior, not answer quality. Application CI
runs the same checks with its own PostgreSQL and Qdrant services.

On Linux distributions not recognized by the pinned Playwright browser installer,
use `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 make smoke-setup` for the
Ubuntu-compatible Chromium build. The runtime still needs Chromium's system
libraries; CI installs them with Playwright's `--with-deps` option.

## The experience

Ask: **“What did we decide about the authentication architecture?”**

ContextMesh selects relevant sources, searches for evidence, expands its search when evidence is missing, and returns an answer with citations. Each answer shows which sources were searched and why another retrieval round ran. If the indexed sources cannot support an answer, the system reports the gap instead of guessing.

Today those sources are uploaded Markdown and text files. Web pages, PDF/Office documents, GitHub, Notion, Slack, and other enterprise connectors follow in later milestones.

### How a question is answered

1. **Plan** — the model sees only the authorized source catalog and proposes sources and up to two queries; the server drops unknown IDs and never widens an explicit source filter.
2. **Retrieve** — Qdrant vector search and PostgreSQL full-text search run in scope, ranks are fused (RRF), and only chunks from live, published document versions are hydrated.
3. **Assess and expand** — the model judges coverage; missing facts trigger reformulated queries or additional sources, bounded to three rounds, a 60,000-token budget, and a 60-second deadline that is checked before every model call and retrieval round (an operation already in progress can run until its own provider timeout).
4. **Answer and verify** — claims may cite only supplied passages; a separate support check flags unsupported claims, which get one repair and are then removed.
5. **Release** — citations are built from canonical records, re-checked for visibility, and saved; a saved answer is withheld if any document it cited or consulted is later deleted.

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

Unstructured parsing, a model-based reranker, and S3-compatible storage are not
implemented yet: Markdown and text use built-in location-preserving parsers, and
reranking uses a deterministic term-coverage baseline. Dependencies are locked in
`backend/uv.lock` and the root `package-lock.json`.

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

Run `make quality-setup`, `make quality`, and `make quality-test` from the repository root. The tooling validates these limits and selected import boundaries; the architecture guardian separately reviews design and behavior. See the [agent workflow](docs/agents.md) and [measurement policy](docs/engineering.md) for details. `make app-test` and `make smoke` check the application behavior separately.

## Portfolio demonstration

Use a synthetic corpus containing an authentication decision record, a platform handbook, and rollout notes. Demonstrate a direct question, a follow-up, an answer that requires a second source, a conflict between document versions, and an unanswerable question. Show citations and measured results against the baseline retrieval system.

The agent and retrieval pipeline exist; the versioned evaluation corpus, judged
questions, and the baseline-versus-agent benchmark do not yet. The conventional
retrieval path sits behind the same `EvidenceSearch` port the agent uses, but no
evaluation harness selects it yet. This ships the agent before the measured
baseline that ADR 006 asks for; record that deviation in the decision log when it
is restored. Deterministic application checks do not establish retrieval quality
or live-model benchmarks.

Known contract gaps in this slice: ingestion mutations do not accept an
`Idempotency-Key` (a retried source creation creates another source; a retried
delete returns 404); source and document listings return at most 200 and 500
items without cursors; readiness checks PostgreSQL but not Qdrant (vector outages
fall back to keyword search); there is no reconciliation job for orphaned vectors
and no document reindex endpoint.
