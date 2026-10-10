# ContextMesh class map

The backend's classes and how they connect. Paths are under `backend/app/`. For each
user story file by file, with its log lines, see [flows.md](flows.md).

## Layers

```mermaid
flowchart LR
  FE["frontend/src<br/>api/client.ts"] -->|HTTP| C
  subgraph C["controllers/"]
    c1["chat.py · sources.py · http.py"]
  end
  subgraph S["services/"]
    s1["ConversationService · SourceService"]
    s2["Agent + AgentWorkflow"]
    s3["HybridRetriever"]
    s4["IngestionWorker · DocumentIndexer<br/>SiteCrawler · Eraser"]
  end
  subgraph D["data/"]
    d1["ChatRepository · SourceRepository<br/>RetrievalRepository · JobRepository<br/>IndexingRepository · CrawlRepository"]
    d2["QdrantVectorIndex · FilesystemBlobStore<br/>OpenAI*/OpenRouter* models · SafeHttpFetcher"]
  end
  C --> s1
  s1 --> s2 --> s3
  s1 --> d1
  s3 --> d1
  s3 --> d2
  s4 --> d1
  s4 --> d2
  W["wiring.py"] -. builds .-> S
  W -. builds .-> D
```

- **controllers** translate HTTP; services are read from `app.state`.
- **services** hold the use cases and call `data` classes directly.
- **data** talks to PostgreSQL, Qdrant, blobs, model providers, and the web.
- **wiring.py** is the only place that picks concrete classes: `build_api()` for
  `main.py`, `build_worker()` for `worker.py`.

## Agent graph

`AgentWorkflow.run()` (`services/agent/graph.py`) runs one LangGraph graph. Each node
calls one `Agent` method (`services/agent/steps.py`); edge labels are the `Agent`
conditions that choose the branch.

```mermaid
flowchart LR
  S(("start")) --> routing
  subgraph routing
    direction TB
    r1["route"] -- "should_answer_direct" --> r2["answer_direct"]
  end
  subgraph research
    direction TB
    p1["plan"] --> p2["retrieve"] --> p3["assess"]
    p3 -- "should_search" --> p2
  end
  subgraph answering
    direction TB
    a1["generate"] -- "has_claims" --> a2["verify"]
    a2 -- "should_repair" --> a3["repair"]
    a3 -- "has_claims" --> a2
  end
  routing -- "direct, or no searchable source" --> rel["release"]
  routing -- "otherwise" --> research --> answering --> rel
  rel --> E(("done"))
```

Every model call goes through `Agent._call()`, which enforces the deadline and token
budget from `AgentPolicy` (`services/agent/state.py`). Prompts and output schemas are
in `services/agent/prompts.py`.

## Asking a question

```mermaid
sequenceDiagram
  participant C as controllers/chat.py
  participant S as ConversationService
  participant R as ChatRepository
  participant A as AgentWorkflow / Agent
  participant H as HybridRetriever
  C->>S: send(identity, conversation, key, message)
  S->>R: replay(key)
  R-->>S: saved answer, or none
  S->>R: claim(key) → Execution
  S->>A: run(question, history, catalog)
  A->>H: search(sources, queries)
  H-->>A: Evidence
  A-->>S: AgentOutcome
  S->>R: complete(execution, outcome)
  R-->>C: TurnResult
```

No database transaction is open while the agent runs. On failure `ChatRepository.fail()`
releases the turn.

## Uploading and indexing a file

```mermaid
sequenceDiagram
  participant C as controllers/sources.py
  participant S as SourceService
  participant SR as SourceRepository
  participant W as IngestionWorker
  participant I as DocumentIndexer
  C->>S: upload(file)
  S->>SR: register_upload → version + index job
  W->>W: JobRepository.claim()
  W->>I: handle(job)
  I->>I: parse (utils/parsers.py), chunk_elements
  I->>I: embed (data/llm.py), upsert (data/qdrant.py)
  I->>I: IndexingRepository.publish()
```

Crawls follow the same pattern: `SiteCrawler` records each page with
`CrawlRepository.register_page`, which enqueues normal indexing.

## Interfaces

Only these have more than one implementation.

| Protocol | Implementations | Defined in |
| --- | --- | --- |
| `ReasoningModel` | `OpenAIReasoningModel`, `TracedReasoningModel` | `services/models.py` |
| `EmbeddingModel` | `OpenAIEmbeddingModel`, `TracedEmbeddingModel` | `services/models.py` |
| `DecisionModel` | `OpenRouterDecisionModel`, `TracedDecisionModel` | `services/models.py` |
| `JobHandler` | `DocumentIndexer`, `SiteCrawler`, `Eraser` | `services/indexing.py` |

## Class finder

| Class | File | Role |
| --- | --- | --- |
| `ConversationService` | `services/chat.py` | Conversations and question turns |
| `SourceService` | `services/sources.py` | Sources, uploads, deletes, evidence |
| `Agent` | `services/agent/steps.py` | Every agent step and its budget |
| `AgentWorkflow` | `services/agent/graph.py` | Runs the LangGraph graph, opens the trace |
| `AgentState`, `AgentPolicy` | `services/agent/state.py` | Run state and server-owned limits |
| `HybridRetriever` | `services/retrieval.py` | Vector + keyword search, fusion, rerank |
| `IngestionWorker` | `services/indexing.py` | Claims a job, runs its handler, records failures |
| `DocumentIndexer`, `Eraser` | `services/indexing.py` | Index a version; clean up after deletes |
| `SiteCrawler`, `Frontier`, `RobotsRules` | `services/crawler.py` | Bounded same-site crawl |
| `ChatRepository` | `data/chat.py` | Conversations, turns, saved answers |
| `SourceRepository` | `data/sources.py` | Sources, documents, versions |
| `RetrievalRepository` | `data/retrieval.py` | Catalog, keyword search, passage hydration |
| `JobRepository` | `data/jobs.py` | Durable job queue with leases |
| `IndexingRepository`, `CrawlRepository` | `data/indexing.py` | Worker writes: publish, crawl results |
| `QdrantVectorIndex` | `data/qdrant.py` | Vector index |
| `FilesystemBlobStore` | `data/blobs.py` | Uploaded and crawled file bytes |
| `OpenAIReasoningModel`, `OpenAIEmbeddingModel`, `OpenRouterDecisionModel` | `data/llm.py` | Model clients |
| `SafeHttpFetcher` | `data/fetcher.py` | Web fetches to public addresses only |
| `LangSmithTracing`, `Traced*` | `data/tracing.py` | Optional LangSmith tracing |
| `ContextMeshError` | `utils/errors.py` | Error code → HTTP status and message |
| `Settings` | `utils/config.py` | Configuration from `.env` |
