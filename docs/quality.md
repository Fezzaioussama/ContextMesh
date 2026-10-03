# Quality, access, and evaluation plan

This document defines implementation and release checks. No checks or benchmarks below have run against an application yet; there is currently only a design baseline.

Repository engineering tooling is now configured separately: `make quality` enforces file size, Python/JS/TS complexity, and selected dependency boundaries; `make quality-test` verifies the enforcement tools. All implementations also require independent architecture review under the [engineering rules](engineering.md) and [agent workflow](agents.md). Tooling results do not constitute application benchmarks.

## Access invariants

1. Workspace membership and resource grants come from trusted server-side identity and canonical records.
2. Source names/descriptions shown to a planner are already authorized.
3. Vector lookup is scoped, and each candidate is checked against current access, tombstones, and its published generation before text hydration.
4. Only authorized hydrated evidence reaches a reranker, context builder, or generation model.
5. Current authorization is rechecked before releasing the answer and when accessing history, evidence, exports, or caches.
6. Model output cannot widen a user-provided source filter or supply an alternate tenant identity.
7. Revocation removes canonical visibility immediately after ContextMesh receives the change; physical vector/blob cleanup can lag. Private enterprise connectors additionally need an explicit upstream permission-freshness policy.

For source-level MVP grants, only the source owner or an authorized editor can upload, sync, reindex, or delete. Readers may query only sources visible through their membership and grants. The fixed development identity is server-configured and restricted to local demonstration.

Retained historical evidence is accessible only while current resource grants permit it and the document is not deleted. If a stored answer depends on inaccessible evidence, withhold that answer on history retrieval. Do not expose it with only its citation removed.

## Input and execution boundaries

| Boundary | Required behavior |
| --- | --- |
| Uploads | Limit bytes, filenames, MIME/type agreement, extracted size, parser time/memory; use opaque blob keys |
| Office/archive documents | Bound decompression and extracted elements; treat malformed inputs as inspectable job failures |
| Website ingestion | Restrict schemes and MIME types; enforce public destinations at connection time and every redirect; block metadata/private endpoints; bound response size and time |
| Parser worker | Run with constrained resources and no provider credentials unrelated to its job |
| Retrieved content | Treat as data; embedded instructions cannot change policy, invoke tools, or request secrets |
| Structured model output | Validate schema, source/evidence IDs, query lengths, call counts, and repair budgets |
| Credentials | Store secret references; redact from logs, errors, event payloads, prompts, and responses |
| Browser rendering | Escape content; sanitize rendered Markdown; validate citation URL schemes |
| Shared deployment | Validate identity tokens and workspace membership; restrict origins and storage access |
| Provider calls | Send only scoped evidence, enforce usage/deadline limits, record the configured data-processing boundary |

Website destination enforcement must defend against DNS rebinding and redirects to disallowed addresses. A one-time hostname resolution is not the control. Disable the website adapter if the execution environment cannot enforce a controlled resolver/egress policy.

Before each shared deployment, configure identity, credential handling, retention, provider usage limits, and permitted source types. These are product requirements for the chosen deployment boundary, not additional steps needed merely to read these design documents.

## Failure and recovery matrix

| Scenario | Expected behavior | Validation |
| --- | --- | --- |
| Worker crashes after claim | Lease expires and another worker resumes idempotently | Restart during a fixture job |
| Worker loses lease but continues | Old fencing token cannot publish or finish | Two-worker race with forced expiry |
| Duplicate upload/sync request | Idempotent resource/job result | Repeat request with same key/hash |
| Reused idempotency key with different input | Reject conflict | Change payload under same key |
| Embedding service times out or rate-limits | Bounded retry; current publication remains valid | Fake timeout/429 with retry delay |
| Partial Qdrant upsert | Pending generation is not published | Fail one indexing batch |
| Qdrant write succeeds, publication crashes | Safe replay or orphan cleanup | Crash immediately before database commit |
| Older version finishes after newer version | Older job cannot replace the requested active generation | Reorder job completion |
| Source deleted while processing | Immediate invisibility; old job cannot resurrect it | Delete between parsing and publication |
| Permission revoked after retrieval | No final answer disclosure; no accessible history replay | Revoke before release, then read history |
| Connector scan stops midway | No false deletion; checkpoint reflects durable work only | Fail pagination halfway |
| Parser returns no text/scanned PDF | Explicit unsupported/OCR-required outcome | Scanned fixture with no text layer |
| Qdrant unavailable | Explicit authorized lexical fallback if configured; otherwise dependency error | Stop vector service during query |
| Chat provider fails | Safe query error; do not report missing evidence as the cause | Fake model error/refusal |
| Planner chooses unknown/inaccessible sources | Server rejects selection within bounded recovery | Malformed structured plan |
| Validator finds unsupported claims | One repair, then remove claims or return an evidence gap | Inject unsupported draft claims |
| Budget or deadline expires | Supported partial result/gap or explicit failure; no unbounded retry | Forced low budgets and delayed calls |
| Stored citation's version was purged | Show evidence unavailable; never substitute newer text silently | Retention fixture |

Canonical publication and query authorization should be tested against real PostgreSQL/Qdrant adapters where behavior depends on transactions, concurrency, filters, or persistence. Model behavior is faked in CI; paid/external model benchmarks are explicitly configured runs.

## Evaluation corpus

Start with a small versioned synthetic corpus that has human-readable ground truth. Include:

- Authentication ADRs with one superseded decision.
- A platform handbook explaining the adopted architecture.
- Rollout notes that contain details missing from the ADR.
- A conflicting draft proposal clearly labeled as a draft.
- A website copy with a known ingestion timestamp.
- Format variants that preserve equivalent text but different page/slide locations.

A first benchmark can contain **40 labeled questions**: 16 direct, 8 requiring multiple sources, 6 follow-ups, 4 conflicting/superseded-evidence cases, and 6 unanswerable questions. Maintain a separate adversarial set for access control, prompt injection, bad IDs, and network destinations; do not dilute functional answer metrics with policy-test counts.

Each question records expected answerability, relevant document/version IDs, supporting passages, required facts, acceptable alternative answers, source scope, and the permitted requester. Relevance judgments should not depend on chunk IDs alone, since changing chunking changes those IDs. Map passage judgments into each evaluated index generation.

Version the corpus, judgments, prompts, code revision, parser/chunker signature, embedding model/dimensions, reranker, chat model, retrieval limits, and budgets. Split development questions from a held-out subset before tuning. Run baseline and agent variants with the same source snapshot and permission state.

## Metrics and preliminary targets

These are proposed gates for the first small corpus, not achieved results or universal quality claims. Report raw counts, answer coverage, and limitations alongside percentages.

| Metric | What it measures | Preliminary gate |
| --- | --- | --- |
| Retrieval Recall@10 | Fraction of labeled relevant passages represented in top ten retrieved chunks | At least 0.85 on answerable evaluation questions |
| Required-fact coverage | Fraction of labeled answer facts correctly represented | Report per question type; compare with baseline |
| Citation reference validity | Citations point to supplied, authorized canonical evidence | 100%; enforced deterministically |
| Supported-claim precision | Human-supported factual claims / returned factual claims | At least 0.95; report answered/partial/abstained counts too |
| Unanswerable handling | Correct evidence-gap results on labeled unanswerable questions | All six in the initial corpus; expand the set before broader claims |
| Scope/permission violations | Any unauthorized evidence, source metadata, history, or derived answer returned | Zero observed in the policy test suite |
| Source-expansion recovery | Multi-source cases where later retrieval recovers the missing evidence | Report counts and unresolved gaps |
| Query latency | End-to-end p50/p95, by baseline/agent and query type | Measure on documented hardware/provider; enforce configured deadline |
| Usage/cost | Tokens, provider requests, estimated cost per completed/interrupted query | Stay within configured per-query budget; compare with baseline |
| Ingestion reliability | Completion/failure rate and time to publication by format | Report with queue depth, size, and retry counts |

“Zero observed” is a release-test result, not proof of universal isolation. A model-assessed support score is not a substitute for human judgments. Citation existence and factual support are reported separately.

Agent adoption requires evidence that the chosen source-routing/expansion policy improves at least a relevant quality or efficiency outcome without unacceptable regressions. Report misses introduced by source selection, extra model usage, and latency as well as successful expansions. A small corpus supports a portfolio demonstration; broader quality claims require a larger, representative held-out dataset.

## Test strategy by layer

| Layer | Meaningful tests |
| --- | --- |
| Domain/application | Version transitions, scope intersection, stable IDs, token budgets, claim/reference rules, idempotent turns |
| Parser/connector contracts | Format locators, pagination replay, revision handling, permissions, complete-scan deletions, explicit unsupported capabilities |
| Persistence/worker integration | Real migrations, atomic enqueue, lease/fencing races, generation publication, restart recovery |
| Search integration | Scoped vector filters, current-version hydration, lexical ranking fixtures, fusion, missing/deleted candidates |
| HTTP/UI | Authentication/scope failures, upload/job progress, citations, source filters, safe errors, stream replay |
| End-to-end | Upload -> publication -> question -> evidence view -> document update -> deletion |
| Evaluation | Real embeddings/models against versioned judgments; separate from deterministic CI tests |

Tests should establish observable requirements and failure recovery, rather than mirroring function implementations. Add them as their corresponding behavior is implemented.

## Observability and release evidence

Record safe trace IDs, job stage/attempts, queue age, processing times, publication/reconciliation failures, retrieved candidate counts before/after visibility checks, searched source IDs, retrieval rounds, context tokens, model usage, and validator outcomes. Keep source text and secrets out of default logs. Public traces expose only authorized action summaries and counts.

The M5 portfolio release should include:

- Accurate clean-start instructions and actual dependency lockfiles.
- Passing CI/integration evidence for the enabled features.
- A reproducible corpus and benchmark report comparing baseline and agent behavior.
- Screenshots or a recording of the real application, including failure/insufficient-evidence behavior.
- Documented supported formats, connector limitations, model/demo mode, retention policy, and deployment boundary.

No benchmark report, passing check, production readiness claim, or implemented connector should be advertised before the corresponding evidence exists.
