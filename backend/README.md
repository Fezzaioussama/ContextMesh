# Backend maintenance guide

ContextMesh's backend is one Python package (`app`) run as two processes: the
FastAPI **API** and the **ingestion worker**. PostgreSQL is canonical for
visibility, versions, publication, jobs, conversations, and citations; Qdrant is
a rebuildable vector projection; uploads live in a blob store.

```text
backend/
  app/
    main.py              # API process: create_app (Uvicorn factory)
    worker.py            # ingestion worker process: `python -m app.worker`
    wiring.py            # picks the concrete classes for both processes
    controllers/         # HTTP: routes and their request/response shapes
      chat.py            #   assistant: conversations, messages, metadata
      sources.py         #   sources, documents, jobs, evidence
      http.py            #   health checks, server identity, safe error responses
    services/            # what the app does, one module per user story
      models.py          #   the values every layer shares (sources, passages, answers…)
      chat.py            #   ask a question: replay or claim the turn, run agent, save
      sources.py         #   add sources, upload, delete, show evidence
      retrieval.py       #   hybrid search + rerank model (term-coverage fallback)
      indexing.py        #   worker loop, indexer, chunking, erasers
      crawler.py         #   bounded website crawl
      validation.py      #   input rules (messages, titles, uploads)
      urls.py            #   URL rules (canonical URLs, crawl scope)
      agent/             #   steps.py (each step), graph.py (LangGraph), state.py, prompts.py
    data/                # everything that stores or fetches data
      db.py              #   PostgreSQL tables, engine, readiness
      chat.py            #   conversations, turns, saved answers
      sources.py         #   sources, documents, versions
      jobs.py            #   durable job queue
      indexing.py        #   worker-side publication and crawl writes
      retrieval.py       #   catalog, keyword search, passage hydration
      qdrant.py, blobs.py, llm.py, fetcher.py, tracing.py
    utils/               # config, errors, logging (flow events), document parsers
  migrations/versions/   # 0001 assistant … 0004 formats and websites
  scripts/start_{api,worker}.sh
  tests/{unit,integration,e2e}/
```

A request flows **controllers → services → data**. Controllers only translate
HTTP; services hold the use cases and call the `data` classes directly; `wiring.py`
is the only place that chooses concrete classes. The model clients (`ReasoningModel`,
`EmbeddingModel`, `DecisionModel` in `services/models.py`) are the only interfaces,
because each has a real adapter, a LangSmith-traced wrapper, and test doubles. Tests
inject replacements through `create_app(settings, services, identity)`.

To follow a user story through the code or the logs, read
[docs/flows.md](../docs/flows.md). Both processes log one line per step, e.g.
`flow=crawl_website step=page_recorded job_id=… url=…`; filter with
`docker compose logs api worker | grep "flow=upload_document"`.

## Ingestion

`POST /api/v1/sources/{id}/documents` validates the file (name, type, size), writes
the blob, then — in one transaction — records an immutable document version and
enqueues `document.index_requested`. Identical content is idempotent. The worker
claims jobs with `FOR UPDATE SKIP LOCKED`, a lease, and a fencing token, then
parses, chunks (heading-aware, token-bounded, deterministic chunk IDs), embeds,
upserts vectors, verifies the vector count, and publishes the generation with a
compare-and-set that rejects deleted documents, newer versions, and lost leases.
Failed reindexing keeps the previous publication; transient failures retry with
backoff up to five attempts. Deleting a document or source hides it immediately;
the worker removes vectors and blobs afterwards.

## Websites

A website source stores a start URL. `POST /api/v1/sources/{id}/sync` enqueues one
crawl job (repeated requests return the open job). The crawler reads `robots.txt`
(RFC 9309: missing allows, unreachable fails and retries), fetches the start page
first and takes the scope from its final address (so http→https or an added `/`
work), then crawls breadth-first within that directory, recording each page as a
document version keyed by its canonical ASCII URL; unchanged pages are idempotent.
Pages are retired only after a complete crawl of more than one page, and only
within the URL prefix that crawl covered; any failure other than 404/410, an empty
page, or a redirect out of scope makes the crawl partial (`crawled_partial`) and
retires nothing. `app/data/web/fetcher.py` resolves every host itself, rejects
non-public addresses (including NAT64 and IPv4-mapped forms) and non-web ports for
each connection and redirect, caps size, and gives each fetch one wall-clock
deadline (twice `CONTEXTMESH_WEB_TIMEOUT_SECONDS`, redirects included).

## Querying

The agent is a bounded LangGraph state machine whose nodes are the `Agent`'s
steps, grouped into `routing`, `research`, and `answering` subgraphs. An optional Jev decision step, given the searchable source names and the
user's recent requests, first routes self-contained conversation to
**direct answer → release**; source-specific, private, current, and uncertain
questions use **plan → retrieve → assess ⟲ expand → answer → verify ⟲ repair
→ release**. Direct answers are explicitly citation-free. Classifier failures and
low-confidence choices conservatively take the retrieval path. Server policy
(`AgentPolicy` in `services/agent/state.py`) limits rounds (3), query
variants (2), repairs (1), claims, context passages, and the deadline (60 s by
default) plus a total token budget; model output can never raise them. Every structured model reply is
schema-checked; unknown source or passage IDs are dropped. Only hydrated passages
from live, published generations reach a model, and retrieved text is treated as
untrusted data. Vector search failures fall back to keyword search; model failures
are reported as failures, never as missing evidence.

Setting `LANGSMITH_TRACING=true` (with `LANGSMITH_API_KEY`) traces every turn in
LangSmith: one `ask_question` run per turn, with each subgraph, node, model call,
and retrieval nested under it. Traces carry full prompts, passages, and answers,
so tracing is off by default and startup fails if it is on without a key.

Answers are persisted with canonical citations (document version, generation,
chunk, heading path, and line range). History and idempotent replay withhold an
answer whose cited document or source was deleted.

## Run and verify

```bash
make app-setup
make db          # PostgreSQL and Qdrant
make migrate
make backend-dev # API
make worker-dev  # ingestion worker
```

`make dev` runs the same processes with Docker Compose. All commands use the
repository `.env`; exported process variables take precedence.

```bash
CONTEXTMESH_TEST_DATABASE_URL=postgresql+psycopg://contextmesh:contextmesh@127.0.0.1:55432/contextmesh_test \
CONTEXTMESH_TEST_QDRANT_URL=http://127.0.0.1:6333 \
  uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
```

Use a dedicated test database. Integration tests apply migrations, create unique
workspaces, and cancel open jobs in that database before and after each test,
because the job queue is shared by all workspaces. Do not run them concurrently
with the browser smoke against the same database. Without the database variable,
integration tests skip; without the Qdrant variable, only the Qdrant server tests
skip (the rest use an in-memory Qdrant).

`make quality` runs Ruff (lint, format, and a cyclomatic complexity limit of 10) and
ESLint.

## Known limits

Scanned documents and images need OCR, which is not implemented; legacy binary
Office formats are not supported. Crawls are bounded and sequential, with no
scheduled re-crawl. Token counts are approximate; chunks are also bounded by
characters.
There is no streaming, reindex endpoint, reconciliation job
for orphaned vectors, or evaluation harness yet. Ingestion mutations do not take
an `Idempotency-Key`, listings are capped without cursors, and readiness does not
check Qdrant. Changing the embedding model requires deleting and re-uploading
documents. The fixed development identity is for local use only.
