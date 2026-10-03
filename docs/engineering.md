# Engineering rules

These rules apply to every implementation role and to the coordinator. The [architecture](architecture.md), [contracts](contracts.md), and [decision log](decisions.md) define the design baseline. The root [AGENTS.md](../AGENTS.md) makes these expectations available to coding agents.

## Hard limits

| Rule | Meaning | Enforcement |
| --- | --- | --- |
| File size | At most **1,000 physical lines** per maintained first-party text file, including blanks/comments | Repository file scanner; ESLint `max-lines` also checks JS/TS |
| Python complexity | At most **4 per function or method**, including nested functions and methods | Radon-based scanner plus Ruff `C901` with maximum 4 |
| JavaScript/TypeScript complexity | At most **4 per function**, including methods, arrow functions, and React components | ESLint `complexity` with `max: 4`, `variant: classic` |
| Dependency direction | Domain/application remain independent of adapters, bootstrap, and external frameworks | Python AST import checks plus architecture review |
| Design quality | SOLID, cohesive modules, appropriate patterns, explicit boundaries | Independent `architecture_guardian` review |

The complexity threshold is a maximum, not a target: simple functions normally have complexity 1. It is not a class/module average and not a limit on the number of LangGraph nodes. Backend and frontend analyzers have language-specific counting conventions; record their diagnostics instead of claiming they compute identical metrics.

Radon counts branches, Boolean operators, comprehensions, and assertions under its documented model. Ruff provides an additional McCabe structural check. See [Radon's metric definitions](https://radon.readthedocs.io/en/latest/intro.html) and [Ruff C901](https://docs.astral.sh/ruff/rules/complex-structure/). ESLint also counts default values, logical operators, optional chaining, and implicit class initialization functions. See [ESLint complexity](https://eslint.org/docs/latest/rules/complexity).

Tests, migrations, developer scripts, Markdown, configuration, and hidden agent files are maintained first-party files and receive the file-size check. Python/JS/TS tests and tooling receive complexity checks too. Generated dependency lockfiles and installed/build artifacts are not hand-maintained code; their precise exclusions are documented below. Binary assets have no LOC metric.

Do not add lint suppressions, lower check coverage, hide code in ignored paths, encode decisions into opaque expressions, or raise thresholds to pass. Refactor by responsibility and behavior. Keep framework initialization and complex work out of module-level executable code. A short facade over one large, tangled helper does not improve the design.

## SOLID in this project

| Principle | Required implementation behavior |
| --- | --- |
| Single responsibility | A module/class has one coherent reason to change. Separate transport validation, use cases, domain policy, and infrastructure I/O. |
| Open/closed | Add a connector/provider through an existing port and registry. Introduce extension points when a concrete variation exists. |
| Liskov substitution | Adapters satisfy the same port preconditions, result types, cancellation, and failure semantics. Unsupported capabilities are explicit. |
| Interface segregation | Prefer focused search, storage, authorization, job, and model ports. A client depends only on operations it uses. |
| Dependency inversion | Domain policy and application use cases depend on domain types and application-defined ports. Bootstrap injects concrete adapters. |

Use Python protocols/value objects for core contracts and transport-specific schemas in HTTP adapters. Avoid passing ORM sessions, FastAPI request objects, LangGraph types, or provider SDK responses through core use cases. UI components consume an API client and typed view models rather than storage or model-provider clients.

## Patterns with a concrete purpose

| Pattern | Suitable use |
| --- | --- |
| Ports and adapters | Connectors, parsers, stores, search engines, model integrations |
| Strategy | Replaceable chunking, reranking, and source-selection policies |
| Repository | Scoped canonical persistence without leaking ORM details |
| Unit of Work | Canonical state changes and durable job insertion in one transaction |
| Registry / factory | Instantiate configured, supported adapters at the composition boundary |
| State machine | Job lifecycle and explicit, bounded retrieval transitions |
| Durable event/job | Decouple ingestion requests from replayable worker execution |

Choose a pattern to resolve an actual dependency or variation. Do not introduce a universal factory, service locator, global mutable singleton, inheritance hierarchy, or abstract base class for every small function. State the purpose of a new abstraction in its review handoff.

## Architecture rules

- `domain` imports only the standard library and its own domain modules.
- `application` imports the standard library, domain types, and application ports/services; framework and persistence imports stay outside it.
- `adapters` implement ports and may use external libraries. Outbound adapters do not depend on inbound transport adapters. Adapters do not import `bootstrap`.
- `bootstrap` is the composition root and may connect layers. Domain/application code cannot resolve concrete adapters through a service locator.
- The LangGraph adapter invokes application services; graph nodes do not own storage or authorization policy.
- Frontend code communicates with the API. It does not import backend internals or hold server credentials.
- Immutable content versions, published index generations, current grants, tombstones, and fencing checks remain canonical in PostgreSQL.
- Candidate text reaches a model only after current access and publication checks. The planner cannot broaden an explicit source filter.

The AST checker catches direct Python imports, including relative imports and imports inside functions/type-checking branches. It is not a whole-program dependency proof: dynamic imports, runtime registries, indirection, circular responsibilities, and business invariants still require reviewer inspection. Static success is necessary but does not prove SOLID or correct authorization.

## Commands

Run from the repository root. Install [uv](https://docs.astral.sh/uv/) and Node.js 24+ first, or use the equivalent Python environment commands below.

```bash
make quality-setup
make quality
make quality-test
```

`quality-setup` installs pinned Python quality tools into the root `.venv` and installs the JavaScript tooling from `package-lock.json`. This environment is for quality checks; future application dependencies live with their backend/frontend projects.

Without uv, create a Python 3.12+ virtual environment with `python3 -m venv .venv`, install `requirements-quality.txt` with `.venv/bin/python -m pip install -r requirements-quality.txt`, then run `npm ci`.

`make quality` runs file/architecture/Python-complexity checks, Ruff lint/format checks, and JS/TS lint/complexity checks. `make quality-test` runs regression tests proving that the gates accept boundary values and reject violations. GitHub Actions runs both commands on pushes and pull requests.

The check reports missing backend source as a skip. That is expected before M1 and is not evidence that the future application follows the architecture. Application unit/integration/end-to-end checks are added during the relevant roadmap milestones.

## File discovery and exclusions

The executable discovery policy lives in [scripts/quality/paths.py](../scripts/quality/paths.py). It scans maintained text including new/untracked and hidden files, independently of Git staging. Read errors and invalid Python syntax are failures. Generated lockfile basenames and explicit installed/build/cache directories are excluded by that policy; source trees, tests, documentation, and `.codex` are included. Symlinks are not followed and are reported as skipped; maintained application code must be regular files within the repository.

Do not expand exclusions or alter enforcement configuration as part of a feature implementation. Changes to quality tooling need regression tests and review by the architecture guardian, with the reason and coverage impact recorded. Generated files must be produced by a documented tool and must not conceal hand-authored application logic.

## Completion and review

A handoff names the implemented requirement, changed files, applicable contracts, SOLID/pattern decisions, commands run, observed results, and remaining limitations. Include the actual paths/lines for any metric or boundary finding.

The coordinator requests an independent architecture review of implementation changes, returns blocking findings to the responsible implementer, and reruns affected checks after repairs. Shared contracts and migration dependencies are sequenced explicitly; parallel writers receive disjoint ownership.

Keep tests behavioral: boundaries, malformed inputs, retries, races, isolation, cancellation, and citation/version integrity. Do not add tests that only assert the implementation was written a particular way. A milestone is complete only with its roadmap acceptance evidence.
