# Implementation roadmap

This roadmap builds one working vertical slice at a time. **M1–M5 produce the local MVP.** M6–M8 extend the source coverage and execution capabilities. Estimates are intentionally omitted until developer availability and the deployment target are known.

Milestones are complete only when their acceptance criteria are demonstrated. Each milestone should produce a reviewable PR with the relevant validation evidence. Task lists below are proposed work, not completed implementation.

## Milestone map

| Milestone | Outcome | Depends on | State |
| --- | --- | --- | --- |
| M0 | Architecture, contracts, evaluation plan, roadmap | — | Documentation delivered |
| M1 | Backend/frontend foundation and persistent job infrastructure | M0 | Planned |
| M2 | Working upload and website ingestion | M1 | Planned |
| M3 | Conventional hybrid RAG with verified citation references | M2 | Planned |
| M4 | Bounded LangGraph planning and retrieval expansion | M3 | Planned |
| M5 | Complete local chat/source UI and portfolio release | M4 | Planned |
| M6 | Public GitHub repository connector | M5 | Planned |
| M7 | Private enterprise identity, permissions, and connectors | M6 | Planned |
| M8 | Registered read-only live API/SQL tools | M7 | Optional |

## M0 — Design baseline

Delivered artifacts:

- [README](../README.md) with the project story and first-release scope.
- [Architecture](architecture.md) with module/runtime boundaries and storage publication rules.
- [Contracts](contracts.md) for connectors, providers, jobs, APIs, and evidence.
- [Decision log](decisions.md) with tradeoffs and revisit conditions.
- [Quality plan](quality.md) with failure cases, access boundaries, and evaluation gates.

Implementation-time choices still to resolve: actual model/provider configuration, pinned dependency versions, deployment identity provider, expected corpus size/languages, and retention/cost limits. None prevents starting the local foundation with fake provider adapters and a fixed server-side development identity.

## M1 — Project foundation and durable work

**Outcome:** one command starts a development environment, and an API request can enqueue work that survives an API/worker restart.

- [ ] Create the planned backend package layout with explicit application ports and a composition root.
- [ ] Set up Python dependency management, formatting/linting, and focused tests; lock dependencies.
- [ ] Create a React/TypeScript/Vite frontend skeleton; lock JavaScript dependencies.
- [ ] Add Compose services for API, worker, frontend, PostgreSQL, and Qdrant; bind the demo to localhost.
- [ ] Add settings validation and `.env.example` containing variable names and safe placeholders.
- [ ] Add Alembic migrations for workspaces, membership, sources, documents, versions, index generations, chunks, jobs, and conversation identities.
- [ ] Implement a server-side development identity and source/workspace scope checks.
- [ ] Implement job enqueue/claim/heartbeat/retry/completion with leases and fencing tokens.
- [ ] Add liveness/readiness routes, safe structured errors, and request/job tracing IDs.
- [ ] Add CI for backend checks, frontend typecheck/build, and relevant integration tests.

**Acceptance:** a clean checkout starts successfully; migrations apply to a fresh database; two workers do not own the same live lease; an expired lease is recoverable; a replaced worker cannot complete a job with an old fencing token; cross-workspace resource lookups fail without revealing metadata.

**Demonstration:** enqueue a fixture job, stop its worker, restart, and show eventual completion without duplicated canonical state.

## M2 — Ingestion vertical slice

**Outcome:** a user can add supported content and observe a published, searchable document generation.

- [ ] Implement upload source registration and bounded multipart upload storage.
- [ ] Implement immutable document identity/version handling and raw blob retention.
- [ ] Add TXT and Markdown parsers with heading/element locators first.
- [ ] Implement deterministic chunk IDs and token-bounded, heading-aware chunking.
- [ ] Implement embedding and vector-index ports with fakes for tests and one real provider adapter.
- [ ] Store pending chunks, upsert Qdrant points, verify writes, and atomically publish the active generation.
- [ ] Add document/source deletion with immediate tombstones and asynchronous cleanup.
- [ ] Add retries, inspectable terminal failures, obsolete-job cancellation, and a reconciliation command/job.
- [ ] Add website registration and single-page HTML ingestion with connection-time destination enforcement, redirect checks, and fetch limits.
- [ ] Add text-bearing PDF, DOCX, and PPTX through the parser adapter, with tested format-specific fixtures and explicit `ocr_required` handling.
- [ ] Expose job/source progress, last successful sync, and safe error codes.

**Acceptance:** each enabled format preserves a source locator; repeat processing creates no duplicate active chunks; a failed first index remains unavailable; failed reindexing preserves the previous publication; a crash between vector writes and PostgreSQL publication recovers safely; deleting a source immediately removes it from retrieval eligibility.

**Demonstration:** ingest the synthetic authentication corpus, inspect its chunks and locators, update a document, and show the new generation replacing the old one. Demonstrate a malformed upload and a blocked private-network URL.

**Scope:** enable a format only after its fixture passes. OCR and recursive crawling remain separate work.

## M3 — Measured conventional RAG

**Outcome:** questions retrieve authorized evidence and return supported claims with usable citations, without source planning yet.

- [ ] Implement PostgreSQL full-text indexing and ranked candidate retrieval for published chunks.
- [ ] Implement scoped Qdrant lookup and canonical active-generation/access checks before text hydration.
- [ ] Fuse candidate ranks with RRF, deduplicate, and add a deterministic baseline reranker.
- [ ] Build token-bounded context with evidence IDs and source diversity controls.
- [ ] Implement structured answer claims and canonical citation rendering.
- [ ] Implement deterministic reference checks and a separately measured claim-support assessor.
- [ ] Implement supported partial answers, contradiction reporting, and insufficient-evidence responses.
- [ ] Add requester-scoped conversations and follow-up question resolution.
- [ ] Add idempotent turn handling and recovery/reporting for interrupted requests.
- [ ] Build the evaluation corpus, relevance judgments, and a repeatable baseline runner.

**Acceptance:** citations resolve to the actual indexed version/location; unsupported reference IDs cannot be returned; explicit source filters remain enforced; provider failures are distinguishable from insufficient evidence; permission revocation blocks evidence and saved answers on the next access; baseline retrieval/answer metrics are recorded using real embeddings.

**Demonstration:** a direct architecture question, a follow-up, a conflicting-source question, and an unanswerable question. Save the benchmark configuration and results for comparison with M4.

## M4 — Agentic retrieval

**Outcome:** the graph can select a relevant subset of eligible sources, identify evidence gaps, and expand search within a strict budget.

- [ ] Implement typed LangGraph state and explicit plan/retrieve/assess/expand/context/generate/validate/release transitions.
- [ ] Provide an authorized source catalog to the planner and validate every returned source ID.
- [ ] Resolve follow-ups into standalone queries without treating earlier assistant statements as evidence.
- [ ] Add evidence-coverage and contradiction assessment.
- [ ] Add controlled source expansion and query reformulation; preserve explicit user scope.
- [ ] Enforce retrieval-round, query-variant, repair, deadline, and token/cost limits in server policy.
- [ ] Permit at most one repair; remove unsupported claims if repair cannot support them.
- [ ] Emit public stage summaries, searched-source lists, timings, and usage.
- [ ] Keep conventional RAG available in the evaluation runner and compare it with the agent graph.

**Acceptance:** a labeled multi-source fixture needs and completes a second retrieval round; a bad plan cannot select an unknown/inaccessible source; repeated low-quality evidence cannot create an unbounded loop; every budget exhaustion yields a supported partial answer or explicit gap; comparison reports quality, latency, rounds, and model usage on the same questions.

**Demonstration:** search an uploaded decision record, recognize a missing rollout detail, expand to rollout notes, and cite both. Show one case where source selection misses evidence and the resulting recovery or limitation.

**Release rule:** retain the graph only with documented quality/cost tradeoffs. “Agentic” is a measured behavior, not a label inferred from using LangGraph.

## M5 — Complete local MVP and portfolio package

**Outcome:** someone can clone the repository, follow accurate setup instructions, and use the complete local workflow.

- [ ] Build a source catalog with upload, URL registration, sync, progress, retry guidance, and deletion.
- [ ] Build a conversation interface with follow-ups and hard source filters.
- [ ] Render citations into an evidence panel with snippet, title, locator, indexed version, and freshness.
- [ ] Stream workflow stages and one validated final answer; make reconnect/replay respect turn state and current access.
- [ ] Add empty/loading/error states, keyboard support, and responsive layouts.
- [ ] Provide an opt-in synthetic demo corpus with clear fixture provenance.
- [ ] If a no-key demo is shipped, label its deterministic extraction/fake embeddings and separate it from real-model benchmarks.
- [ ] Add end-to-end coverage for upload -> ingestion -> chat -> citation -> deletion.
- [ ] Add actual local setup instructions, troubleshooting, screenshots, and a short demo script.
- [ ] Publish benchmark reports with corpus version, provider settings, costs/usage assumptions, and limitations.
- [ ] Validate clean startup, graceful shutdown, persistent data, and restart recovery.

**Acceptance:** fresh local setup works as documented; all enabled MVP formats and website ingestion work through the UI; citations remain accurate after document updates; deletion and permission checks pass through API, chat, history, and evidence views; the quality plan's release gates have evidence.

**Deployment boundary:** the fixed-identity demo is local. Shared hosting requires the production authentication and credential controls from M7 before exposure. Website ingestion must meet its egress controls before it is enabled in any environment.

## M6 — First external connector: public GitHub

**Outcome:** ingest one configured public repository through the existing connector protocol.

- [ ] Add bounded repository-content discovery, including documentation files and a selected branch/revision.
- [ ] Preserve commit-based provenance for reproducible citations; source branch labels are supplementary.
- [ ] Implement checkpoints, stable external IDs, duplicate-page replay, and deletion handling after complete scans.
- [ ] Respect rate limits and expose synchronization freshness/errors.
- [ ] Add issues/PR discussions only after repository-document ingestion works, preserving their distinct provenance.
- [ ] Add connector contract tests and a multi-source benchmark that combines repository content with uploads.

**Acceptance:** a second sync indexes changes without duplicating unchanged content; renamed/deleted items reconcile correctly; interrupted pagination cannot delete unseen documents; repository citations identify the ingested revision; private repositories remain unavailable until private-source authorization is implemented.

## M7 — Private enterprise access and connectors

**Outcome:** private sources are searchable only according to current, mapped permissions.

- [ ] Add production OIDC authentication and validated workspace membership/roles.
- [ ] Implement secret references, encryption/secret-store integration, token refresh, reconnect, and revocation.
- [ ] Define mappings between provider identities/groups and ContextMesh principals; do not equate a connector's fetch access with every user's read access.
- [ ] Add document-level grants, permission-change synchronization, freshness limits, and fail-closed access when permission state is stale.
- [ ] Enforce permission rechecks on retrieval, final answers, history, evidence, and any cache.
- [ ] Add private GitHub using the completed identity/permission foundation.
- [ ] Implement Notion next; implement Slack only after channel/thread permission and deletion behavior are tested.
- [ ] Prioritize Drive, Confluence, and S3-compatible connectors by an actual use case, using the same contracts.
- [ ] Add sync health, audit events, retention/deletion controls, and a deployment runbook.

**Acceptance:** a revoked user cannot access previously indexed evidence or saved derived answers on the next request; group-membership changes are reflected; credential expiry fails safely and is visible; incomplete scans cannot imply deletion; upstream permission freshness follows the documented policy; connector behavior passes contract tests.

**Expansion rule:** implement and evaluate one private connector at a time. Do not register planned integrations as functional source types.

## M8 — Optional live API and SQL tools

**Outcome:** the agent can use registered read-only tools when indexed evidence cannot answer an explicitly supported question type.

- [ ] Define a tool registry with typed schemas, capability metadata, workspace scope, and allowed resources.
- [ ] Implement server-side argument validation, deadlines, row/result limits, and usage budgets.
- [ ] For SQL, use a read-only database role, statement controls, and approved views/tables; do not rely on a model instruction as the execution boundary.
- [ ] For APIs, configure fixed destinations and permitted operations; apply the same egress boundary as website fetching.
- [ ] Normalize returned data into evidence records with provenance, timestamps, and citation identity.
- [ ] Add access, injection, timeout, and partial-result tests before making tools selectable.

**Acceptance:** model-proposed out-of-scope calls cannot execute; results are traceable and citable; a failed live tool produces an explicit gap or failure; retrieval and execution remain within the user's authorization and request budget.

## First implementation PRs

Start in this order, splitting each item if it becomes difficult to review:

1. **`chore: bootstrap ContextMesh development environment`** — package skeleton, frontend shell, lockfiles, Compose, settings, health routes, CI.
2. **`feat: add scoped canonical data model`** — migrations, request identity, repositories, source ownership, immutable versions/generations.
3. **`feat: add durable ingestion jobs`** — transactional enqueue, worker leases, fencing, retries, recovery.
4. **`feat: ingest Markdown and text documents`** — upload storage, parser/chunker, fake-backed pipeline, canonical publication.
5. **`feat: add persistent vector indexing`** — real embedding adapter, Qdrant writes, reindex/delete/reconcile.
6. **`feat: add hybrid retrieval with citations`** — lexical search, fusion, evidence hydration, structured claims, validation, baseline evaluation.

Use acceptance criteria from each milestone in the corresponding PR. Add task-specific tests as behavior is implemented; this planning repository does not include placeholder passing tests or invented benchmark results.
