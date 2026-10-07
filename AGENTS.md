# ContextMesh engineering and agent rules

## Scope and source of truth

ContextMesh is an Agentic RAG modular monolith with separate API and ingestion
worker processes. Read the relevant contracts before changing code:

- `docs/architecture.md`: target layout, dependencies, publication, and access rules.
- `docs/contracts.md`: domain vocabulary, ports, events, and transport contracts.
- `docs/decisions.md`: proposed architectural baseline and revisit conditions.
- `docs/roadmap.md`: milestone scope and observable acceptance criteria.
- `docs/quality.md`: product behavior, failure cases, and evaluation requirements.
- `docs/engineering.md`: mandatory engineering limits and automated checks.
- `docs/agents.md`: configured roles and the coordination workflow.
- `docs/flows.md`: each user story traced file by file, with its log lines.
- `backend/README.md`: the backend folder layout and what belongs where.

Work only on the assigned milestone and files. Configuring agents and checks does
not authorize application implementation. Planned integrations and tests must not
be described as implemented or passing before evidence exists.

## Hard limits

- Every maintained first-party text file has **at most 1,000 physical lines**,
  including blank lines and comments. Exclusions for generated and vendored
  artifacts are explicit in the engineering policy; do not invent exemptions.
- Every Python function or method, including nested functions, has **cyclomatic
  complexity at most 4**, measured by the configured Radon and Ruff C901 gates.
- Every JavaScript/TypeScript function or method, including nested functions,
  has **cyclomatic complexity at most 4**, measured by ESLint's classic variant.
- These thresholds are ceilings, not targets. Refactor into cohesive operations
  when a limit is approached. Do not hide branches in clever expressions, add
  suppression comments, exclude ordinary source code, or split files solely to
  manipulate a metric.
- Agents cannot waive limits, weaken rules, or widen exclusions to pass a task.
  Explain a conflict to the coordinator and propose a compliant design.

## Design obligations

Follow SOLID through concrete responsibilities and behavior: cohesive components,
extensions behind stable contracts, substitutable adapters, small consumer-focused
ports, and dependencies on abstractions at application boundaries. Apply each
principle where relevant; avoid unnecessary interfaces and class hierarchies.

Use patterns to solve named problems. Prefer ports/adapters and dependency
injection for integrations, repositories for persistence boundaries, Strategy for
interchangeable behavior, and explicit state transitions for workers and queries
when these fit the accepted architecture. Explain any new pattern's purpose.

The backend (`backend/app`) is organized as `controllers -> services -> data`:

- `controllers/` translate HTTP (routes, `schemas/`, error mapping) and call
  services only.
- `services/` hold the use cases, one area per user story. They own their
  interfaces in `services/ports/` and must not import `data`, `controllers`, or
  `setup`. `services/rules/` holds pure business rules and imports only the
  standard library, other rules, and `utils.exceptions`/`utils.security`.
  LangGraph is used only in `services/agent/graph.py`, which sequences agent steps.
- `data/` implements the ports: PostgreSQL (`db`), Qdrant (`vectors`), blobs,
  model clients (`llm`), and the guarded web fetcher (`web`).
- `utils/` (config, errors, security, logging, parsers) may be used by every
  layer and imports nothing from `controllers`, `data`, or `setup`.
- `setup/` is the only place that chooses concrete classes for the API and worker.

Every user-story step logs a `flow_event` (`app/utils/logging.py`) with ids and
counts only, and `docs/flows.md` is updated when a flow changes. The frontend
uses HTTP contracts and never imports backend internals.

Preserve ingestion/query separation, typed boundaries, canonical PostgreSQL
visibility and publication, bounded agent execution, authorization checks, and
canonical citations. Use the ADR process for substantive architecture changes;
an ADR does not silently override a user requirement or a hard limit.

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

Run `make quality` for automated limits and dependency rules. Run
`make quality-test` when configuring or changing the quality tooling. Run the
meaningful application tests, type checks, builds, and integrations applicable to
the implemented behavior; these exist only after their corresponding milestone
creates them. Do not invent placeholder successes or imply these two commands
test an application that has not been implemented.

Choose tests from observable behavior, contract guarantees, and failure modes.
Avoid tests that merely mirror private methods or reversible documentation edits.
Document any unavailable command, dependency, or runtime and its consequence.

Each handoff states changed files, behavior, design rationale, exact checks and
outcomes, unresolved limitations, and any requested contract changes. Review
handoffs use `PASS`, `FAIL`, or `BLOCKED` when required evidence is unavailable,
with actionable findings carrying `file:line` references and measurable evidence.
