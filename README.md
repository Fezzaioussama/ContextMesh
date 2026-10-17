<p align="center">
  <img src="docs/assets/contextmesh-mark.svg" width="128" height="128" alt="ContextMesh — connected knowledge nodes forming a mesh">
</p>

<h1 align="center">ContextMesh</h1>

<p align="center"><strong>Agentic knowledge retrieval across your documents and websites.</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/architecture-modular_monolith-101c22" alt="Architecture: modular monolith">
  <img src="https://img.shields.io/badge/API-FastAPI-168278" alt="API: FastAPI">
  <img src="https://img.shields.io/badge/UI-React_%2B_TypeScript-168278" alt="UI: React and TypeScript">
  <img src="https://img.shields.io/badge/search-PostgreSQL_%2B_Qdrant-168278" alt="Search: PostgreSQL and Qdrant">
</p>

ContextMesh is an extensible Agentic RAG platform for conversational search. It
ingests files or bounded website crawls, combines vector and keyword retrieval,
and returns evidence-backed claims with canonical citations. When the available
sources do not support an answer, it reports the evidence gap instead of guessing.

> **Project status:** agentic retrieval works end to end, with streamed answers
> and an offline evaluation command. OCR for scanned documents, enterprise
> connectors, and a baseline-versus-agent benchmark remain planned. See
> [current limits](#current-limits-and-roadmap).

## Quick start

Install Docker with Compose, then run from the repository root:

```bash
make dev
```

This creates `.env` from [.env.example](.env.example) only when one does not
already exist, applies database migrations, and starts the frontend, API,
ingestion worker, PostgreSQL, and Qdrant.

1. Open [http://localhost:5173](http://localhost:5173).
2. Choose **Sources** and create a **Files** or **Website** source.
3. Upload documents, or provide a website start URL for an immediate crawl.
4. When the documents show **Ready**, ask a question.

The API documentation is available at
[http://localhost:8000/docs](http://localhost:8000/docs). Model-backed answers
require the [OpenRouter configuration](#model-configuration) below; without it,
the UI shows setup instructions and never invents replies.

## What it does

- **Ingests diverse knowledge:** text-bearing PDF, modern Office, HTML,
  Markdown, text, data, code, and bounded website crawls.
- **Searches with evidence:** Qdrant vector search and PostgreSQL full-text
  search are fused, reranked, and hydrated only from live published versions.
- **Researches within bounds:** the agent selects authorized sources, rewrites
  queries, expands incomplete searches, and stops at fixed round, token, and time
  budgets.
- **Validates every grounded answer:** claims may cite only supplied passages;
  unsupported claims get one repair attempt and are then removed.
- **Keeps citations canonical:** saved citations are re-checked for visibility,
  and answers are withheld after a cited or consulted document is deleted.
- **Preserves conversation scope:** Each conversation keeps its own source
  selection; a new one asks whether to keep the current selection, and is named
  by the model after its first question.

### Supported sources

| Kind | Formats | Citation location |
| --- | --- | --- |
| Documents | PDF with a text layer, Word `.docx`, PowerPoint `.pptx`, Excel `.xlsx` | Page, heading path, slide, or sheet |
| Web and markup | HTML `.html`/`.htm`, Markdown `.md` | Heading path and source lines |
| Text, data, and code | `.txt`, `.csv`, `.json`, `.yaml`, `.xml`, `.log`, `.rst`, `.sql`, and common code files | Source lines |
| Websites | A start URL; pages below the same path are crawled | Page URL and heading |

Scanned PDFs without a text layer are reported as **OCR required**. Legacy
`.doc`, `.ppt`, and `.xls` files and images are not supported. Uploads are
limited to 20 MB by default (`CONTEXTMESH_MAX_UPLOAD_BYTES`), and Office files
are checked for decompression bombs before they are opened.

## How a question becomes an answer

1. **Route** — optional Jev classification sends self-contained requests to a
   direct answer. Source-specific, current, private, and uncertain requests take
   the grounded path; unavailable or low-confidence decisions also fall back to
   retrieval.
2. **Plan** — the model sees only the authorized source catalog and proposes
   sources plus the standalone question, up to three rephrasings or
   sub-questions, up to two keyword queries, and a hypothetical answer used only
   to find similar passages. Unknown IDs are dropped, and an explicit source
   filter is never widened.
3. **Retrieve and rerank** — question variants use Qdrant vector search; keyword
   variants use PostgreSQL full-text search. Weighted reciprocal-rank fusion
   combines them; the hypothetical answer carries half weight. Questions are
   embedded as queries, while passages and the hypothetical answer are embedded
   as documents. A rerank model scores canonical hydrated candidates, and the
   chat model grades the strongest passages from 0–3. If model grading fails,
   retrieval order is preserved; if vector search fails, keyword search remains.
4. **Assess and expand** — missing facts can trigger another search or additional
   in-scope sources, bounded to three rounds, a 60,000-token budget, and a
   75-second deadline that also caps model-call duration.
5. **Answer and verify** — grounded claims can cite only supplied passages. A
   separate support check flags unsupported claims, which receive one repair and
   are then removed. Direct answers are explicitly citation-free.
6. **Release** — canonical citations and the validated answer are saved. An
   idempotency key replays the saved turn instead of running it again.

The interface also shows which sources were searched and why another retrieval
round ran. Try asking: **“What did we decide about the authentication
architecture?”**

## Architecture

ContextMesh is a modular monolith with a FastAPI API and a separate ingestion
worker. Both processes share one Python package, while ingestion and querying
keep independent execution paths. PostgreSQL is canonical; Qdrant is a
rebuildable vector projection.

```mermaid
flowchart LR
    User[React UI] --> API[FastAPI API]
    Files[Files and websites] --> API
    API --> Jobs[(PostgreSQL + jobs)]
    Jobs --> Worker[Ingestion worker]
    Worker --> Blob[Raw files]
    Worker --> Q[(Qdrant)]
    API --> Agent[Bounded LangGraph agent]
    Agent --> Jobs
    Agent --> Q
    Agent --> Models[OpenRouter models]
    Agent --> Answer[Validated answer + citations]
    Answer --> User
```

The backend follows `controllers → services → data`; `wiring.py` is the only
place that chooses concrete classes. LangGraph only sequences agent steps, and
the frontend consumes typed HTTP contracts rather than backend internals.

| Responsibility | Current choice |
| --- | --- |
| UI | React, TypeScript, Vite |
| API and use cases | Python, FastAPI |
| Agent orchestration and optional tracing | LangGraph; LangSmith when enabled |
| Canonical state and keyword retrieval | PostgreSQL, SQLAlchemy, Alembic |
| Vector retrieval | Qdrant |
| Raw document storage | Local filesystem adapter |
| Model provider | OpenRouter for reasoning, embeddings, reranking, and optional Jev routing |
| Development | Docker Compose with Python and JavaScript lockfiles |

Dependencies are locked in `backend/uv.lock` and the root `package-lock.json`.
For file-by-file flow traces, see [docs/flows.md](docs/flows.md). For backend
boundaries and the data model, see [backend/README.md](backend/README.md).

## Configuration and operations

### Model configuration

OpenRouter is the only model provider. Set these server-only values in `.env`,
then run `make dev` again:

```dotenv
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_MODEL=deepseek/deepseek-v4.1-flash
OPENROUTER_EMBEDDING_MODEL=voyageai/voyage-4-large
OPENROUTER_RERANK_MODEL=voyageai/rerank-3
CONTEXTMESH_DECISION_ENABLED=true
CONTEXTMESH_DECISION_MODEL=typesafe/jev-1.13
CONTEXTMESH_MAX_OUTPUT_TOKENS=8192
```

With decision routing enabled, Jev sees the names and descriptions of indexed
sources and classifies each turn before retrieval. It uses the same server-side
`OPENROUTER_API_KEY`.

Optional LangSmith tracing is off by default because traces contain full prompts,
passages, and answers. Setting `LANGSMITH_TRACING=true` requires
`LANGSMITH_API_KEY`; startup fails instead of silently enabling incomplete
tracing when the key is absent.

Reasoning models can spend several thousand hidden reasoning tokens per agent
step, so keep `CONTEXTMESH_MAX_OUTPUT_TOKENS` at 8192. The maximum is 16384; it
is a ceiling, not a charge. Exhausting it returns `model_output_limit`, not a
partial answer.

Each embedding model has its own Qdrant collection. After changing it, restart the
worker (`make dev` does): it re-indexes existing documents on startup, and each
stays keyword-only until its new index is published. The rerank model can change freely. Set `OPENROUTER_RERANK_MODEL=` to use
deterministic query-term coverage instead.

### Website crawl safeguards

Crawls remain on the start URL's scheme, host, and directory, respect
`robots.txt`, and follow links up to `CONTEXTMESH_CRAWL_MAX_DEPTH` (default 2)
and `CONTEXTMESH_CRAWL_MAX_PAGES` (default 50). They fetch only HTML, text,
Markdown, and PDF.

Every connection, including redirects, is checked at connect time. Private,
loopback, link-local, cloud-metadata addresses, and non-web ports are refused.
**Crawl again** re-fetches the site; unchanged pages are skipped, and missing
pages are retired only after a crawl finishes without limits or transient errors.

### Local development

For hot reload, install Python 3.12+, uv, and Node.js 24+:

```bash
make app-setup
make db          # PostgreSQL and Qdrant
make migrate
make backend-dev
# In other terminals:
make worker-dev
make frontend-dev
```

Backend code is in [backend](backend) and the UI in [frontend](frontend). The
root [Dockerfile](Dockerfile) builds both: its `backend` stage runs the Compose
API and worker, and its final stage is the single production image.

`make stop` stops the containers while retaining PostgreSQL, Qdrant, and upload
volumes. Only localhost ports are published: frontend `5173`, API `8000`,
PostgreSQL `55432`, and Qdrant `6333`. The fixed local development identity is
not suitable for shared hosting.

### Deploy to Railway

[docs/deploy-railway.md](docs/deploy-railway.md) sets up the app, PostgreSQL, and
Qdrant on Railway. [railway.toml](railway.toml) builds the root Dockerfile into one
service that serves the frontend, the API, and the ingestion worker. The deployment
has no login, so anyone with its URL can use it.

## Verification

```bash
make app-test
make smoke-setup
make smoke
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

Backend integration tests use real PostgreSQL and an in-memory Qdrant. Qdrant
server tests run when `CONTEXTMESH_TEST_QDRANT_URL` is set. The browser smoke
runs the frontend, API, worker, PostgreSQL, and Qdrant against a controlled local
Responses and Embeddings fixture. It does not contact a paid model, so it proves
integration behavior, not answer quality. Application CI uses the same checks
with its own PostgreSQL and Qdrant services.

On Linux distributions not recognized by the pinned Playwright installer, use:

```bash
PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 make smoke-setup
```

The runtime still needs Chromium's system libraries; CI installs them with
Playwright's `--with-deps` option.

`make quality` runs Ruff for linting, formatting, and Python complexity, plus
ESLint for TypeScript/JavaScript complexity and the 1,000-line file limit.
`make app-test` runs backend and frontend tests plus the frontend build;
`make smoke` drives the real application in a browser.

## Current limits and roadmap

Implemented today:

- uploads for PDF with a text layer, DOCX, PPTX, XLSX, HTML, Markdown, text,
  data, and common code files;
- bounded same-path website crawling, ingestion progress, retries, immutable
  document versions, deletion, and cleanup;
- hybrid retrieval, two-stage reranking, bounded search expansion, source
  filters, canonical citations, and explicit insufficient-evidence responses;
- source ownership checks, retrieval isolation tests, and end-to-end browser
  smoke coverage.

Not implemented yet:

- OCR and image ingestion, legacy binary Office formats, scheduled re-crawls,
  user accounts and login, enterprise OAuth/connectors, and live SQL/API tools;
- Unstructured-based parsing, S3-compatible raw storage, distributed services,
  a reindex endpoint, orphaned-vector reconciliation, and a measured baseline.

Supported formats currently use built-in location-preserving parsers rather than
Unstructured.

Other known contract gaps: ingestion mutations do not accept an
`Idempotency-Key` (a retried source creation creates another source; retrying a
completed delete returns 404); source and document listings are capped at 200
and 500 items without cursors; readiness checks PostgreSQL but not Qdrant, whose
outages fall back to keyword search. Crawls are bounded and sequential. Token
counts are approximate, and chunks are also bounded by characters.

### Evaluation status

`make eval` runs the served agent over a JSONL golden dataset and scores
routing, abstention, document recall and precision, quote recall, budget, and
LLM-judged faithfulness and correctness. [backend/evals](backend/evals/README.md)
holds a fictional starter corpus with 13 cases; `--fail-under` thresholds can gate
CI, and `--langsmith` logs each run as an experiment. A baseline-versus-agent
benchmark, which would run `HybridRetriever` on its own, does not exist yet. This
ships the agent before the measured baseline requested by ADR 006; that deviation
should be recorded in the decision log when the benchmark is restored.

## Documentation

- [User-story flows](docs/flows.md) — every story traced file by file, including
  its structured log events.
- [Backend guide](backend/README.md) — backend boundaries, ingestion, websites,
  querying, and verification.
- [Frontend guide](frontend/README.md) — browser workspace and commands.
- [Agent evaluation](backend/evals/README.md) — datasets, metrics, and reports.
- [Deploy to Railway](docs/deploy-railway.md) — services, variables, and checks.

Project-scoped Codex roles in [.codex/agents](.codex/agents) cover backend,
ingestion, retrieval, frontend, QA, and independent architecture review.
