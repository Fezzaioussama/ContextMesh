# Offline agent evaluation

`python -m app.evaluate` runs the same agent the API serves over a golden dataset,
scores every run, and writes a report. It reads the development workspace
(`CONTEXTMESH_DEV_SUBJECT`, `CONTEXTMESH_DEV_WORKSPACE_ID`) and never writes to the
database. The flow, file by file, is `evaluate_agent` in
[docs/flows.md](../../docs/flows.md).

```text
evals/
  corpus/              # three Markdown documents about Tidepool, a fictional product
  datasets/starter.jsonl
  reports/             # one folder per run (git-ignored)
```

## 1. Index the corpus

The corpus states facts no model knows (prices, response targets, release dates), so
only retrieval can answer. `tidepool-release-notes.md` also contains a prompt-injection
line that a good agent ignores.

Start the stack (`make dev`, or `make db`, `make migrate`, `make backend-dev`, and
`make worker-dev`), then create a source and upload the three files, either in the UI
or through the API:

```bash
SOURCE_ID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/sources \
  -H 'Content-Type: application/json' \
  -d '{"name": "Tidepool", "description": "Tidepool plans, support policy, and release notes"}' \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
for file in backend/evals/corpus/*.md; do
  curl -s -F "file=@$file;type=text/markdown" \
    "http://127.0.0.1:8000/api/v1/sources/$SOURCE_ID/documents" > /dev/null
done
curl -s "http://127.0.0.1:8000/api/v1/sources/$SOURCE_ID/documents"  # wait for "searchable": true
```

Case expectations name documents by title (the file name), not id, so uploading the
corpus again or reindexing it keeps the dataset valid.

## 2. Run

Set `OPENROUTER_API_KEY` in `.env`. Set `OPENROUTER_JUDGE_MODEL` to a model other than
`OPENROUTER_MODEL`: a model grading its own answers is lenient, and the CLI warns when
they match. Direct routing needs `CONTEXTMESH_DECISION_ENABLED=true`; without the
classifier every question retrieves, and the `direct` cases fail `route_correct`.

```bash
make eval                                              # the starter dataset
make eval ARGS="--repeats 3 --tag pricing --source Tidepool"
cd backend && uv run --locked python -m app.evaluate --dataset evals/datasets/starter.jsonl
```

| Option | Meaning |
| --- | --- |
| `--dataset PATH` | JSONL dataset (required) |
| `--repeats N` | runs per case, to see how stable the agent is (default 1) |
| `--concurrency N` | runs at once (default 4) |
| `--tag T` | only cases with this tag; repeatable, any tag matches |
| `--source NAME` | search only this source; repeatable; default every searchable source |
| `--fail-under METRIC=VALUE` | exit 1 when the metric's mean is below VALUE; repeatable |
| `--output DIR` | report folder (default `backend/evals/reports/<UTC time>/`) |
| `--langsmith` | also log the run as a LangSmith experiment (needs `LANGSMITH_API_KEY`) |

Exit codes: `0` every threshold holds, `1` a threshold fails, `2` the dataset, an option,
the configuration (such as a missing OpenRouter key), or the catalog (no searchable
source) is invalid, and `3` the evaluation failed for another reason, such as LangSmith
rejecting the upload after the report was written; its message names only the failure's
code. A failed agent run or judge call never stops the evaluation: it is recorded on that
run.

## 3. Read the report

`report.md` lists the settings, the mean of every metric, the quality means per tag, the
thresholds, and every run that fell short with the reason (an agent error, a judge
error, or each quality score below 1). `report.json` holds the same summary plus every
run: status, answer, cited documents, gaps, trace, metrics, and errors.

A mean only counts the runs where the metric applies; `n/a` means it never applied.

| Metric | Meaning | Applies when |
| --- | --- | --- |
| `completed` | 1 when the agent returned an answer, 0 on an agent error | always |
| `route_correct` | the agent took `expected_route` (only a direct response is direct) | always |
| `abstention_correct` | an unanswerable case got `insufficient_evidence` or `partial`; an answerable retrieve case got `answered` or `partial` | not an answerable direct case |
| `document_recall` | share of `expected_documents` the answer cites | documents are expected |
| `document_precision` | share of cited documents that are expected | documents are expected and cited |
| `quote_recall` | share of `expected_quotes` found in the passages the answer was drafted from (ignoring case and spacing) | quotes are expected |
| `faithfulness` | judge: share of claims their cited passages support | the answer has claims |
| `correctness` | judge: grade 0 to 3 against `reference_answer`, divided by 3 | a reference is given |
| `total_tokens`, `latency_seconds`, `search_rounds`, `repairs` | the agent's budget per run (judge tokens are reported separately) | the agent answered |

Quote recall separates the two halves of a wrong answer: a low value means retrieval
missed the passage; a high value with low correctness means the answer misused it.

## 4. Write good cases

One JSON object per line; a bad line stops the run with its line number.

```json
{"id": "harbor-annual-price", "question": "How much does the Harbor plan cost per user when billed annually?", "expected_route": "retrieve", "answerable": true, "expected_documents": ["tidepool-plans.md"], "expected_quotes": ["$14 per user per month billed annually"], "reference_answer": "$14 per user per month when billed annually.", "tags": ["single-doc", "pricing"]}
```

Required: `id` (unique, stable: LangSmith matches examples by it), `question`,
`expected_route` (`retrieve` or `direct`), and `answerable`. Optional: `history` (earlier
`{"role", "content"}` messages for follow-ups), `expected_documents`, `expected_quotes`,
`reference_answer`, and `tags`.

- Ask about facts only your documents hold; general knowledge hides retrieval failures.
- Keep quotes short and verbatim, so chunking cannot split them.
- Include unanswerable questions next to answerable ones on the same topic, so the agent
  must tell a near miss from an answer.
- Write the reference answer a careful person would accept, not the one wording you
  expect; the judge grades meaning.
- Tag by capability (`multi-doc`, `follow-up`, `injection`) to see per-tag regressions.
- Use `--repeats 3` or more before trusting a small difference between two runs.

## 5. Gate changes in CI

```bash
uv run --project backend --locked python -m app.evaluate \
  --dataset backend/evals/datasets/starter.jsonl \
  --fail-under completed=1 --fail-under route_correct=0.9 \
  --fail-under faithfulness=0.9 --fail-under correctness=0.7
```

A threshold holds when the metric's mean is at least the value; a metric that applied to
no run fails its threshold. Only quality metrics take thresholds. The job needs the
indexed corpus and real model calls, so run it where the stack and credentials exist.

## LangSmith

`--langsmith` syncs the dataset to LangSmith under the file's name (`starter`), adding
an example for each case id it lacks (an edited case keeps its first example), and logs
the run as an experiment whose feedback is exactly the metrics in `report.json`. The
agent and judge run once, locally; the experiment replays their recorded output.
Without the flag nothing is sent to LangSmith, even with `LANGSMITH_TRACING=true`; with
it, `LANGSMITH_TRACING=true` also traces every agent run as an `ask_question` run, as the
API does.
