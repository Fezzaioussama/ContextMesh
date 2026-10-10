# User-story flows

Each section follows one user story from the browser to the database, file by
file, and lists the log lines it writes. All paths are under `backend/app/`.

Both processes print one line per step, with ids and counts only (never document
text, questions or answers):

```text
2026-10-07 22:41:03 context_mesh.flow flow=upload_document step=accepted document_id=… job_id=… duplicate=False
```

To follow one story, filter by its name. To follow a single run, filter by its id:
every line written during a worker job carries its `job_id`, and every line of a
question carries its `conversation_id` (and `turn_id` once the turn has started),
even when several runs overlap:

```bash
docker compose logs -f api worker | grep "flow=crawl_website"
docker compose logs api worker | grep "job_id=<id>"
docker compose logs api | grep "turn_id=<id>"
```

Every request has three layers: **controllers** (HTTP in and out) → **services**
(the use case) → **data** (PostgreSQL, Qdrant, blobs, model and web clients).
Work that takes time is a durable job in PostgreSQL that the worker picks up.

| Flow name | User story | Starts in |
| --- | --- | --- |
| `add_source` | Create a source (files or website) | API |
| `upload_document` | Upload a file and get it indexed | API, then worker |
| `crawl_website` | Crawl a website source | API, then worker |
| `ask_question` | Ask a question and get a cited answer | API |
| `delete_document` / `delete_source` | Remove a document or a source | API, then worker |

---

## `add_source`: create a source

1. `controllers/sources.py` `create_source`: `POST /api/v1/sources` with
   name, description, and optional `url`.
2. `services/sources.py` `SourceService.create`: a `url` makes a website source,
   validated by `services/rules/web.py` `checked_start_url` (public http(s), no
   credentials or custom port, ASCII form).
3. `data/db/repositories/source_repository.py` `create` saves the row.

Log: `step=created source_id=… kind=upload|website`.

## `upload_document`: upload a file and get it indexed

**API (fast, synchronous)**

1. `controllers/sources.py` `upload_document`:
   `POST /api/v1/sources/{id}/documents` (multipart). Rejects bodies over the
   declared size limit early.
2. `services/sources.py` `SourceService.upload`:
   - `services/rules/uploads.py` `checked_upload` checks name, type and size;
   - `data/blobs/filesystem.py` writes the bytes;
   - `data/db/repositories/source_repository.py` `register_upload` (via
     `version_records.py` `record_version`) stores an immutable document version
     and enqueues a `document.index_requested` job in the same transaction.
     Identical content is a duplicate and creates no job.
3. Response `202` with the document and `job_id`. The frontend polls
   `GET /api/v1/sources/{id}/documents` until the document is searchable.

Log: `step=accepted document_id=… job_id=… duplicate=…`.

**Worker (background)**

4. `services/ingestion/worker.py` `IngestionWorker.run_once` claims the job
   (`data/db/repositories/job_repository.py`, `FOR UPDATE SKIP LOCKED`, lease
   and fencing token).
5. `services/ingestion/indexer.py` `DocumentIndexer.handle`:
   - parse with the parser for the media type (`utils/parsers/registry.py`:
     PDF, Word, PowerPoint, Excel, HTML, Markdown/text);
   - chunk with `services/rules/chunking.py` (never across headings, pages or
     slides);
   - embed (`data/llm/embeddings.py`) and write vectors (`data/vectors/qdrant.py`);
   - `data/db/repositories/indexing_repository.py` `publish` makes the new
     generation visible, unless the document was deleted, replaced, or the lease
     was lost.

Logs: `step=job_started job_id=… attempt=1`, then `step=job_ended` or
`step=job_failed code=… retry=True|False` (`lease_lost` if another worker took the
job over). `step=job_cancelled code=obsolete` before `job_ended` means the document
was deleted or replaced meanwhile, so nothing was indexed. Failure codes appear in
the UI (for example `ocr_required` for scanned PDFs).

## `crawl_website`: crawl a website source

**API**

1. `controllers/sources.py` `sync_source`: `POST /api/v1/sources/{id}/sync`.
2. `services/sources.py` `SourceService.sync` →
   `source_repository.py` `request_sync` enqueues `source.sync_requested`
   (or returns the crawl already open).

Log: `step=requested source_id=… job_id=…`.

**Worker**

3. `services/ingestion/crawler.py` `SiteCrawler.handle`:
   - `RobotsRules` reads `robots.txt` (missing: allowed; unreachable: the job
     fails and retries);
   - the start page is fetched first; its final address sets the scope
     (`services/rules/web.py` `crawl_scope`);
   - `Frontier` walks links breadth-first within the scope, depth and page limits;
   - each page goes through `data/web/fetcher.py` `SafeHttpFetcher` (public
     addresses only, size cap, wall-clock deadline) and is stored by
     `data/db/repositories/crawl_repository.py` `register_page` as a document
     version, which enqueues normal indexing (see `upload_document`, worker part);
   - `crawl_repository.py` `finish` retires pages missing from a **complete**
     crawl, only within the part of the site it covered. A partial crawl ends with
     stage `crawled_partial` and removes nothing.

Logs (all with the crawl's `job_id`): `step=job_started`, then per page
`step=page_recorded url=…` (new content), `step=page_unchanged url=…`, or
`step=page_skipped url=… code=…`; then `step=crawled pages=… complete=…` and
`step=job_ended` (or `job_failed`, e.g. `code=robots_disallowed`). Page URLs are
logged without their query string, which may carry tokens.

## `ask_question`: ask a question and get a cited answer

1. `controllers/chat.py` `send_message`:
   `POST /api/v1/assistant/conversations/{id}/messages` with an
   `Idempotency-Key` header.
2. `services/chat_service.py` `ConversationService.send` →
   `services/assistant.py` `Assistant.send`:
   - a repeated key returns the saved answer
     (`data/db/repositories/turn_repository.py` `replay`);
   - otherwise the turn is claimed (`claim`), the agent runs, and the answer with
     its citations is saved (`complete`).
3. The agent (`services/agent/graph.py`, steps in `services/agent/steps.py`):
   **plan** (`planner.py`) → **retrieve** (`gather.py`, hybrid search in
   `services/retrieval.py`: PostgreSQL full text + Qdrant, fused and reranked) →
   **assess** (`assessor.py`, may search again) → **generate** (`writer.py`) →
   **verify** (`checker.py`) → **repair** (once, if needed) → **release**
   (`release.py`). Limits (rounds, tokens, deadline) are in `policy.py`; model
   calls go through `data/llm/openai.py`.

Logs (all with `conversation_id`, and `turn_id` from `turn_started` on):
`step=turn_started`, one `step=agent_plan`, `agent_retrieve`,
`agent_assess`, `agent_generate`, `agent_verify`, `agent_repair` per graph step,
then `step=answered status=… citations=… tokens=…` or `step=failed code=…`.
A replay logs `step=replayed`. A question refused before a turn starts (unknown
source filter, a turn already in progress, a reused key with another message)
logs only `step=rejected code=…`.

## `delete_document` / `delete_source`

1. `controllers/sources.py` `delete_document` / `delete_source`
   (`DELETE /api/v1/documents/{id}`, `DELETE /api/v1/sources/{id}`).
2. `services/sources.py` → `source_repository.py` hides the item at once (it
   disappears from search and answers) and enqueues a cleanup job.
3. Worker: `services/ingestion/erasers.py` `DocumentEraser` / `SourceEraser`
   remove vectors and blobs.

Logs: `step=requested … job_id=…`, then `step=job_started` / `job_ended`.

---

## Where to look for…

| Question | File |
| --- | --- |
| Which URL maps to which function? | `controllers/router.py`, `controllers/*.py` |
| What does the API return? | `controllers/schemas/` (mirrored in `frontend/src/api/contracts.ts`) |
| What business rule applies? | `services/rules/` |
| Which concrete class is used? | `setup/` (API in `setup/api.py`, worker in `setup/worker.py`) |
| What is stored and how? | `data/db/models/`, `data/db/repositories/`, `migrations/versions/` |
| Settings and limits | `utils/config.py`, `.env.example` |
