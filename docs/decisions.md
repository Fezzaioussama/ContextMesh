# Architecture decision log

Status: **proposed design baseline**, 2026-10-03. These decisions guide implementation planning and can be revised when requirements or measured results change.

## ADR 001 — Modular monolith with inward dependency boundaries

**Decision:** use one Python backend package with domain, application, and adapter layers. Run API and worker as separate processes from that package.

**Reason:** ingestion and retrieval need different execution characteristics, while one codebase keeps contracts, transactions, and local development manageable.

**Tradeoff:** modules need enforced boundaries because process separation alone does not prevent coupling. Domain/application services remain independent of HTTP, database, graph, and provider SDK implementations.

**Revisit when:** a module has a distinct scaling bottleneck, operational owner, deployment cadence, or failure-isolation requirement demonstrated by measurements.

## ADR 002 — Separate ingestion from query execution

**Decision:** all source fetching, document processing, embedding, and index publication happen through durable worker jobs. Chat reads published evidence.

**Reason:** parsing and embedding are slower and more failure-prone than lookup. A document can be processing while the current published version remains available.

**Tradeoff:** users need visible ingestion state and freshness. A newly uploaded document cannot answer questions until publication finishes.

**Revisit when:** a use case specifically requires a fresh live lookup. Add a bounded, authorized live tool; keep bulk ingestion separate.

## ADR 003 — PostgreSQL durable jobs before a broker

**Decision:** commit canonical changes and a typed job row in the same transaction. Consume jobs with short row-lock claims, leases, heartbeats, fencing tokens, and idempotent handlers.

**Reason:** this gives recoverable background delivery without adding Redis, Celery, or a second message system to the first release. Jobs are the MVP's durable event mechanism.

**Tradeoff:** polling has latency and PostgreSQL carries queue load. At-least-once delivery requires careful replay behavior. This is a work queue, not a general-purpose publish/subscribe platform.

**Revisit when:** measured queue load affects database performance, independent consumers require fan-out, or scheduling/routing requirements justify a broker. Preserve transactional event recording and add an outbox dispatcher to the broker.

## ADR 004 — Qdrant vectors plus PostgreSQL lexical retrieval

**Decision:** use Qdrant for vector candidates, PostgreSQL full-text search for lexical candidates, and application-level reciprocal rank fusion.

**Reason:** keywords and exact terms complement embedding similarity. PostgreSQL already owns the canonical chunks, so lexical retrieval initially needs no extra search service.

**Tradeoff:** native PostgreSQL text ranking is not BM25. Fusion spans two stores, and candidate hydration needs active-generation checks. A reranker is a separate, measurable stage.

**Revisit when:** the corpus benchmark shows lexical recall problems or the operational cost of split retrieval dominates. Compare a sparse/BM25 Qdrant adapter or another dedicated lexical index using the same judged questions.

## ADR 005 — PostgreSQL owns visibility; vector indexes are projections

**Decision:** keep immutable content versions, separate index generations, current grants, tombstones, and active-generation pointers in PostgreSQL. Store identifiers and filter metadata in vector payloads; hydrate evidence through canonical checks.

**Reason:** metadata and vector writes cannot be assumed atomic. Canonical publication prevents partial or obsolete generations from becoming evidence, and current permissions gate content independently of stale vector payloads.

**Tradeoff:** vector candidates need a scoped database join before model processing. Stale points can consume retrieval slots until cleaned up. Reconciliation and bounded overfetching become operational requirements.

**Revisit when:** a measured bottleneck requires a different storage strategy; preserve the authorization and version-visibility invariants in any redesign.

## ADR 006 — Bounded agency after a measured RAG baseline

**Decision:** ship conventional hybrid retrieval first, then add LangGraph source planning, evidence assessment, and bounded expansion. Keep the baseline selectable in the evaluation harness.

**Reason:** source planning can improve evidence selection but can also skip useful sources, increase latency, and spend more tokens. Its benefit should be demonstrated on the same corpus and questions.

**Tradeoff:** the first retrieval milestone is not yet agentic. The eventual agent remains constrained by server-derived scope, explicit budgets, and deterministic policy checks.

**Revisit when:** evaluation shows a different planning strategy is better. Do not assume more model calls improve retrieval.

## ADR 007 — Provider ports and honest demo behavior

**Decision:** isolate chat, embeddings, and reranking behind typed ports. Select and pin actual provider/model configurations during implementation. Use fakes for tests and an optional clearly labeled no-key demonstration.

**Reason:** core use cases should not depend on a specific SDK, credential format, or model. Fake embeddings and extractive demo answers make deterministic testing possible.

**Tradeoff:** structured output, token limits, dimensions, and refusal behavior differ between providers and require adapter tests. A fake-backed demo does not establish semantic retrieval quality or LLM agent quality.

**Revisit when:** a provider capability requires extending the port. Keep real-model evaluations separate from deterministic test results.

## ADR 008 — Indexed sources before live execution tools

**Decision:** first implement upload and individual-page connectors. Add public GitHub next, then private enterprise connectors after permission mapping and credential lifecycle work. Live SQL/API tools are a separate later capability.

**Reason:** a normalized indexed path provides reusable retrieval, provenance, and citation behavior. Enterprise permissions and arbitrary live actions introduce different identity and execution requirements.

**Tradeoff:** the first release covers a subset of the long-term vision. Connector state, revisions, and capabilities must still support later incremental sync and revocation.

**Revisit when:** an explicitly prioritized integration is required. It must satisfy the common ingestion, permission, and evidence contracts before entering the query catalog.
