# Backend maintenance guide

ContextMesh's backend is one Python package (`app`) run as two processes: the
FastAPI **API** and the **ingestion worker**. PostgreSQL is canonical for
visibility, versions, publication, jobs, conversations, and citations; Qdrant is
a rebuildable vector projection; uploads live in a blob store.

```text
backend/
  app/
    main.py                     # Uvicorn factory (API)
    worker.py                   # `python -m app.worker` (ingestion worker)
    domain/                     # Pure values and policy: answers, knowledge,
                                #   chunking, ranking (RRF), uploads, validation
    services/                   # Use cases; owns every port
      ports/                    # conversations, sources, ingestion, retrieval, models
      assistant.py              # Authorize scope, claim turn, run agent, release
      sources.py                # Sources, uploads, deletion, evidence
      retrieval.py              # Hybrid retrieval + deterministic reranker
      ingestion/                # Worker loop, indexer, erasers
      agent/                    # Policy, state, planner, gatherer, assessor,
                                #   writer, checker, release, steps
    ai/
      llm/                      # OpenAI-compatible Responses + Embeddings adapters
      orchestration/graph.py    # LangGraph adapter over agent steps
    db/                         # SQLAlchemy tables and repositories
    search/qdrant.py            # Vector index adapter
    parsers/blocks.py           # Markdown/plain-text parser with locators
    storage/filesystem.py       # Blob store with opaque keys
    api/ schemas/               # HTTP transport and published shapes
    bootstrap/                  # Composition roots for API and worker
  migrations/versions/          # 0001 assistant, 0002 knowledge, 0003 consulted docs
  scripts/start_{api,worker}.sh
  tests/{unit,integration,e2e}/
```

Dependencies point inward: `domain` uses only the standard library and the
shared `core` kernel; `services` depend on `domain` and their own ports; adapters
(`ai`, `db`, `search`, `parsers`, `storage`, `api`) implement those ports;
`bootstrap` is the only place concrete adapters are chosen. Tests inject
replacements through `create_app(settings, services, identity)`.

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

## Querying

The agent is a bounded LangGraph state machine whose nodes are application
services: **plan → retrieve → assess ⟲ expand → answer → verify ⟲ repair →
release**. Server policy (`services/agent/policy.py`) limits rounds (3), query
variants (2), repairs (1), claims, context passages, and the deadline (60 s by
default) plus a total token budget; model output can never raise them. Every structured model reply is
schema-checked; unknown source or passage IDs are dropped. Only hydrated passages
from live, published generations reach a model, and retrieved text is treated as
untrusted data. Vector search failures fall back to keyword search; model failures
are reported as failures, never as missing evidence.

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

Files stay at most 1,000 physical lines and functions at cyclomatic complexity 4
(Radon and Ruff C901). Independent architecture review remains required alongside
automated checks.

## Known limits

Only Markdown and plain text are ingested; website, PDF, and Office formats are
not enabled. Token counts are approximate; chunks are also bounded by characters.
There is no streaming, model-based reranker, reindex endpoint, reconciliation job
for orphaned vectors, or evaluation harness yet. Ingestion mutations do not take
an `Idempotency-Key`, listings are capped without cursors, and readiness does not
check Qdrant. Changing the embedding model requires deleting and re-uploading
documents. The fixed development identity is for local use only.
