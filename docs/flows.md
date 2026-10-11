# User-story flows

Each user story, file by file, with the log lines it writes. Paths are under
`backend/app/`. For the classes and diagrams, see [guide.md](guide.md).

Both processes log one line per step, with ids and counts only (never document text,
questions, or answers). Filter by flow name, or by `job_id` / `turn_id` to follow one
run:

```bash
docker compose logs -f api worker | grep "flow=crawl_website"
docker compose logs api worker | grep "job_id=<id>"
```

| Flow | User story | Runs in |
| --- | --- | --- |
| `add_source` | Create a source (files or website) | API |
| `upload_document` | Upload a file and get it indexed | API, then worker |
| `crawl_website` | Crawl a website source | API, then worker |
| `ask_question` | Ask a question, get a direct or cited answer | API |
| `delete_document` / `delete_source` | Remove a document or source | API, then worker |

## `add_source`

1. `controllers/sources.py` `create_source` (`POST /api/v1/sources`).
2. `services/sources.py` `SourceService.create`: a `url` makes a website source,
   checked by `services/urls.py` `checked_start_url`.
3. `data/sources.py` `SourceRepository.create`.

Log: `step=created source_id=… kind=…`

## `upload_document`

1. `controllers/sources.py` `upload_document` (`POST /api/v1/sources/{id}/documents`).
2. `SourceService.upload`: `services/validation.py` `checked_upload` checks name, type,
   and size; `data/blobs.py` stores the bytes; `SourceRepository.register_upload`
   records a version and its index job in one transaction (identical content is a
   duplicate, with no job).
3. Worker: `services/indexing.py` `IngestionWorker` claims the job (`data/jobs.py`),
   and `DocumentIndexer` parses (`utils/parsers.py`), chunks, embeds (`data/llm.py`),
   writes vectors (`data/qdrant.py`), and publishes (`data/indexing.py`), unless the
   document was deleted or replaced meanwhile.

Logs: `step=accepted document_id=… job_id=… duplicate=…`, then `job_started`,
`job_ended` or `job_failed code=… retry=…`. `job_cancelled code=obsolete` means
nothing was indexed; `lease_lost` means another worker took over.

## `crawl_website`

1. `controllers/sources.py` `sync_source` (`POST /api/v1/sources/{id}/sync`) →
   `SourceRepository.request_sync` enqueues a crawl, or returns the open one.
2. Worker: `services/crawler.py` `SiteCrawler` reads `robots.txt`, fetches the start
   page (its final URL sets the scope), then walks links breadth-first within scope,
   depth, and page limits. Each page is fetched by `data/fetcher.py` (public addresses
   only) and saved by `CrawlRepository.register_page`, which enqueues indexing.
3. `CrawlRepository.finish` removes pages missing from a **complete** crawl only; a
   partial crawl (`crawled_partial`) removes nothing.

Logs: `step=requested`, then `page_recorded`, `page_unchanged`, or
`page_skipped code=…` per page, then `crawled pages=… complete=…`. URLs are logged
without their query string.

## `ask_question`

1. `controllers/chat.py` `send_message`
   (`POST /api/v1/assistant/conversations/{id}/messages`, `Idempotency-Key` header).
2. `services/chat.py` `ConversationService.send`: a repeated key returns the saved
   answer; otherwise `ChatRepository` (`data/chat.py`) claims the turn, the agent
   runs with no transaction open, and the answer and citations are saved.
3. The agent (`services/agent/`) is one LangGraph run. **route** (Jev classifier)
   picks a path; uncertain or unavailable routing retrieves:
   - grounded: plan (standalone question, rewrites, keywords, hypothetical answer)
     → retrieve (`services/retrieval.py`: vector + keyword, weighted RRF) → rerank
     (model grades passages) → assess (may search again) → generate → verify →
     repair (once) → release;
   - direct (greetings, rewriting, general reasoning): direct → release, no
     citations.
4. With `LANGSMITH_TRACING=true`, the turn is one LangSmith run `ask_question`
   (`data/tracing.py`). The worker is never traced.

Logs: `turn_started`, `agent_<step>` per step, `route_decided route=… p_direct=…`,
then `answered status=… citations=… tokens=…` or `failed code=…`. A replay logs
`replayed`; a refusal before the turn starts logs `rejected code=…`.

## `delete_document` / `delete_source`

1. `controllers/sources.py` (`DELETE /api/v1/documents/{id}`, `/sources/{id}`).
2. `SourceRepository` hides the item at once and enqueues a cleanup job.
3. Worker: `services/indexing.py` `Eraser` removes vectors and blobs.

Logs: `step=requested … job_id=…`, then `job_started` / `job_ended`.

## Where to look

| Question | File |
| --- | --- |
| Which URL runs which function? | `controllers/chat.py`, `controllers/sources.py`, `controllers/http.py` |
| What does the API return? | Response models in `controllers/*.py`, mirrored in `frontend/src/api/contracts.ts` |
| Which input rule applies? | `services/validation.py`, `services/urls.py` |
| Which concrete class is used? | `wiring.py` |
| What is stored? | `data/db.py`, `migrations/versions/` |
| What does an error code mean? | `utils/errors.py` |
| Settings and limits | `utils/config.py`, `.env.example` |
| Why did the agent answer that way? | LangSmith run `ask_question`, filtered by `turn_id` |
