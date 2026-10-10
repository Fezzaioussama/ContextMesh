# ContextMesh engineering and agent rules

## Scope and source of truth

ContextMesh is an Agentic RAG modular monolith with separate API and ingestion
worker processes. Read the relevant guides before changing code:

- `docs/flows.md`: each user story traced file by file, with its log lines.
- `backend/README.md`: the backend folder layout and what belongs where.

Work only on the assigned task and files. Planned integrations and tests must not
be described as implemented or passing before evidence exists.

## Limits

- Every maintained first-party text file has **at most 1,000 physical lines**.
- Every Python and JavaScript/TypeScript function has **cyclomatic complexity at
  most 10**, checked by Ruff C901 and ESLint's classic variant.
- Do not add suppression comments, exclude ordinary source code, or split functions
  or files only to satisfy a metric.

## Design

Keep the code simple and direct. Prefer one readable function or class over extra
layers, and keep a helper next to its only caller. Add an interface (`Protocol`)
only when real alternative implementations exist; today that is the three model
clients in `services/models.py` (real adapter, LangSmith-traced wrapper, test
doubles). Tests substitute plain duck-typed doubles.

The backend (`backend/app`) is organized as `controllers -> services -> data`:

- `controllers/` translate HTTP (routes, request/response models, error mapping)
  and call services only.
- `services/` hold the use cases, one module per user story, and call `data`
  classes directly. `services/models.py` holds the values every layer shares.
  LangGraph is confined to `services/agent/graph.py`, which only sequences the
  agent's steps.
- `data/` talks to PostgreSQL, Qdrant, blobs, model providers, and the web.
- `utils/` holds config, errors, logging, and document parsers; it imports nothing
  from `controllers` or `data`.
- `wiring.py` is the only place that chooses concrete classes for the API and
  worker.

Every user-story step logs a `flow_event` (`app/utils/logging.py`) with ids and
counts only, and `docs/flows.md` is updated when a flow changes. The frontend
uses HTTP contracts and never imports backend internals.

Preserve ingestion/query separation, typed boundaries, canonical PostgreSQL
visibility and publication, bounded agent execution, authorization checks, and
canonical citations.

## Coordination

The main agent coordinates work; configured roles do not recursively create
coordinator teams. Delegate to the roles in `.codex/agents/` when agent work is
explicitly requested or required by an assigned implementation task.

Before delegation, provide the milestone, acceptance criteria, exact file
ownership, contracts to consume, dependencies, and required verification. Assign
disjoint files and keep **at most three child agents active**. Complete shared
contracts first, then delegate their consumers. Sequence changes to shared files
instead of letting two roles overwrite them.

Implementation roles stay within their assigned files. If another owner's file
must change, send a concrete request to the coordinator. Preserve staged and
unstaged user work. Do not stage, commit, publish, or contact third parties without
task authorization. Ask for clarification only when independent progress cannot
resolve an actual ambiguity.

Use `architecture_guardian` for an **independent review of implementation
changes**, separate from the agent that wrote them. Include the changed paths,
diff, milestone, acceptance criteria, and check evidence. The reviewer does not
edit the implementation. Static checks enforce selected measurable constraints;
they do not establish SOLID compliance, appropriate patterns, or sound design.

The coordinator collects review findings, assigns fixes, reruns relevant checks,
and requests another review when findings are resolved. A blocking review finding
or missing required verification prevents declaring the task complete. Do not
convert routine review into an additional user approval flow.

## Verification and handoff

Run `make quality` for lint, formatting, and the limits above, and `make app-test`
for the backend and frontend tests and the frontend build; add `make smoke` for
changes that cross the API, worker, and browser. Do not invent placeholder
successes.

Choose tests from observable behavior, contract guarantees, and failure modes.
Avoid tests that merely mirror private methods or reversible documentation edits.
Document any unavailable command, dependency, or runtime and its consequence.

Each handoff states changed files, behavior, design rationale, exact checks and
outcomes, unresolved limitations, and any requested contract changes. Review
handoffs use `PASS`, `FAIL`, or `BLOCKED` when required evidence is unavailable,
with actionable findings carrying `file:line` references and measurable evidence.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
