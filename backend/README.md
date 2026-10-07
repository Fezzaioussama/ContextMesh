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
    controllers/                # HTTP: routes (chat, sources, health), error mapping
      schemas/                  #   request/response shapes published to the frontend
    services/                   # What the app does, one area per user story
      sources.py                #   add sources, upload, delete, show evidence
      assistant.py              #   ask a question: claim turn, run agent, save answer
      retrieval.py              #   hybrid search + deterministic reranker
      agent/                    #   plan, gather, assess, write, check, release; graph.py
      ingestion/                #   worker loop, indexer, website crawler, erasers
      ports/                    #   interfaces the services need (stores, models, web)
      rules/                    #   pure business rules: chunking, ranking, citations,
                                #     uploads, URLs, validation (no frameworks or I/O)
    data/                       # Everything that stores or fetches data
      db/                       #   SQLAlchemy tables and repositories (PostgreSQL)
      vectors/                  #   Qdrant vector index
      blobs/                    #   uploaded/crawled file storage
      llm/                      #   OpenAI-compatible model and embedding clients
      web/                      #   guarded HTTP fetcher (public destinations only)
    utils/                      # config, errors, security, logging (flow events)
      parsers/                  #   Markdown/text, HTML, PDF, Word, PowerPoint, Excel
    setup/                      # Wiring: picks concrete data classes for API and worker
  migrations/versions/          # 0001 assistant … 0004 formats and websites
  scripts/start_{api,worker}.sh
  tests/{unit,integration,e2e}/
```

A request flows **controllers → services → data**. Controllers only translate
HTTP; services hold the use cases and depend on `services/ports` interfaces, which
the `data` classes implement; `services/rules` stays free of frameworks and I/O so
it is easy to read and test; `utils` is shared by everyone; `setup` is the only
place that chooses concrete classes. Tests inject replacements through
`create_app(settings, services, identity)`.

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

Scanned documents and images need OCR, which is not implemented; legacy binary
Office formats are not supported. Crawls are bounded and sequential, with no
scheduled re-crawl. Token counts are approximate; chunks are also bounded by
characters.
There is no streaming, model-based reranker, reindex endpoint, reconciliation job
for orphaned vectors, or evaluation harness yet. Ingestion mutations do not take
an `Idempotency-Key`, listings are capped without cursors, and readiness does not
check Qdrant. Changing the embedding model requires deleting and re-uploading
documents. The fixed development identity is for local use only.
