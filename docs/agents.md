# ContextMesh implementation agents

The project has six reusable coding-agent roles. They implement the application described in the design documents; they are separate from the LangGraph agent that will eventually run inside ContextMesh.

## Roles and ownership

| Role | Responsibility | Default ownership |
| --- | --- | --- |
| `backend_engineer` | Domain/application foundations, API adapters, persistence, composition | Assigned backend foundation/API/persistence files and migrations |
| `ingestion_engineer` | Connectors, parsers, chunking, worker reliability, publication and cleanup | Assigned ingestion services and their adapters/tests |
| `retrieval_engineer` | Hybrid search, context building, bounded graph, claims and citations | Assigned retrieval/answer services, graph adapters, and evaluations |
| `frontend_engineer` | Source/chat/evidence interface and typed API integration | Assigned frontend features and browser tests |
| `qa_engineer` | Behavioral validation, failure recovery, isolation, evaluation and tooling | Assigned tests/evaluation/quality files; reports product defects to their owner |
| `architecture_guardian` | Independent review of architecture, SOLID, patterns, file sizes and complexity | Read-only review; findings and verification reports |

The main agent coordinates the work through [AGENTS.md](../AGENTS.md). It assigns a milestone/task, acceptance criteria, explicit file ownership, shared contracts, and the required validation. Specialists do not start unrelated milestones or create further agent trees by default.

All roles inherit the current session's model/reasoning settings. The repository does not select a paid model, store credentials, or change the user's global Codex configuration.

## Codex configuration

[.codex/config.toml](../.codex/config.toml) enables agents and caps active child threads at three. Each role is a standalone TOML definition under `.codex/agents/`, with a name, description, and role instructions. This uses the project-scoped custom agent format documented in [official OpenAI documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).

Start a new Codex session from this repository so it discovers the configuration and root instructions. Project configuration loading follows the client's trust/managed settings. Clients that do not load custom roles can still use the role instructions when delegating; do not claim a role-specific sandbox is active without verifying it.

The guardian requests read-only execution and is instructed not to edit code or approve its own implementation. Parent runtime permission overrides can take precedence over role defaults, so file ownership and independent review remain required. See the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

For a local interactive CLI session:

```bash
codex
```

Ask the coordinator to use the named roles. In a CLI that supports it, `/agent` shows spawned threads. Custom role configuration is loaded for new sessions; adding these files does not reconfigure an already-running chat automatically.

## Suggested implementation request

```text
Implement M1 from docs/roadmap.md using the ContextMesh agents.
Assign backend foundation work to backend_engineer and the frontend
skeleton to frontend_engineer, with disjoint file ownership.
Have qa_engineer verify the applicable acceptance criteria.
Use architecture_guardian for independent review after integration.
Enforce AGENTS.md and docs/engineering.md: files <=1000 physical lines,
cyclomatic complexity <=4 per function/method, SOLID, and hexagonal
dependency boundaries. Fix findings and run make quality and
make quality-test before reporting completion.
```

This is an example request for a future implementation turn. Configuring the agents has not started M1.

## Coordination workflow

1. Read the architecture, contracts, decisions, roadmap, quality plan, and engineering rules.
2. Define the shared contract changes first. Assign independent implementation slices and reserve shared files for the coordinator or one named owner.
3. Run no more than three child agents concurrently. Sequence dependent migrations, schema changes, composition, and integration.
4. Collect each specialist's handoff and validation evidence; integrate the changes.
5. Have QA run the applicable behavior checks and the numeric/import gates.
6. Have the guardian independently inspect the integrated diff and relevant existing code. Static gate success is evidence, not a substitute for semantic architecture review.
7. Send blocking findings back to the owner, repair, rerun affected checks, and re-review. Report verified results and any unavailable checks candidly.

## Guardian report contract

The reviewer returns `PASS`, `FAIL`, or `BLOCKED` with the reviewed scope and commands/results. `BLOCKED` means required evidence is missing, not that the architecture passed.

For each blocking finding, report:

- Rule/invariant violated and the file/line.
- Concrete behavior or dependency that breaks it.
- A repair that preserves responsibility and contracts.
- A meaningful verification step after repair.

Evaluate SOLID, appropriateness of patterns, dependency direction, scope boundaries, index publication, job fencing/idempotence, source authorization, citation provenance, budgets, and the roadmap acceptance criteria applicable to the change. Do not approve a metric workaround or manufacture a violation from code that is not in scope.

## Automated enforcement

```bash
make quality-setup
make quality
make quality-test
```

The [engineering rules](engineering.md) define measurement and exclusions. These commands check repository code/tooling now and automatically cover future source files when introduced. They do not substitute for application tests or demonstrate a working RAG platform.
