"""LangSmith datasets and experiments for the offline agent evaluation.

Only `python -m app.evaluate --langsmith` uses this, because examples and experiments
carry questions, answers, and scores. Each dataset file becomes one LangSmith dataset whose
examples are keyed by case id; each evaluation becomes one experiment on it. The agent and
the judge run locally first, so an experiment replays the recorded runs and publishes the
scores the local metrics computed: LangSmith and `report.json` always agree.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from langsmith import Client
from langsmith.schemas import Example

DATASET_DESCRIPTION = "ContextMesh golden cases, synced by `python -m app.evaluate`."

Record = Mapping[str, object]


@dataclass(frozen=True)
class Experiment:
    """A finished local evaluation to publish.

    Attributes:
        name: The experiment name prefix; LangSmith appends a unique suffix.
        runs: Each case's recorded runs, in repetition order, keyed by case id. A record
            holds the case's `metrics` among its other outputs.
    """

    name: str
    examples: tuple[Example, ...]
    runs: Mapping[str, Sequence[Record]]
    repeats: int
    metadata: Mapping[str, object]


class LangSmithExperiments:
    """Syncs evaluation datasets to LangSmith and logs experiments on them."""

    def __init__(self, *, api_key: str, endpoint: str, client: Client | None = None):
        self._client = client or Client(api_key=api_key, api_url=endpoint)

    def synced(self, dataset: str, examples: Mapping[str, Record]) -> tuple[Example, ...]:
        """Return the dataset's examples for these cases, adding the ones it lacks.

        The dataset is created when missing. An example is matched by the `case_id` in its
        inputs, so re-running never duplicates one; an edited case keeps its first example.

        Args:
            dataset: The LangSmith dataset name.
            examples: Each case's example (`inputs` with its `case_id`, `outputs`, and
                `metadata`), keyed by case id.
        """
        client = self._client
        if client.has_dataset(dataset_name=dataset):
            dataset_id = client.read_dataset(dataset_name=dataset).id
        else:
            dataset_id = client.create_dataset(dataset, description=DATASET_DESCRIPTION).id
        known = {item.inputs.get("case_id") for item in client.list_examples(dataset_id=dataset_id)}
        missing = [example for case_id, example in examples.items() if case_id not in known]
        if missing:
            client.create_examples(dataset_id=dataset_id, examples=missing)
        stored = client.list_examples(dataset_id=dataset_id)
        return tuple(item for item in stored if item.inputs.get("case_id") in examples)

    def logged(self, experiment: Experiment) -> str:
        """Publish the recorded runs as one experiment and return its name."""
        # Imported here so the API, which imports this module through wiring, never loads
        # LangSmith's evaluation framework.
        from langsmith import evaluate

        pending = {case_id: deque(runs) for case_id, runs in experiment.runs.items()}

        def target(inputs: dict) -> dict:
            """The case's next recorded run: nothing runs again for LangSmith."""
            return dict(pending[inputs["case_id"]].popleft())

        results = evaluate(
            target,
            data=list(experiment.examples),
            evaluators=[published_scores],
            experiment_prefix=experiment.name,
            metadata=dict(experiment.metadata),
            num_repetitions=experiment.repeats,
            max_concurrency=0,  # Sequential, so each case's runs replay in recorded order.
            client=self._client,
        )
        return results.experiment_name

    def close(self) -> None:
        """Send runs and feedback still buffered by the background uploader."""
        self._client.flush()


def published_scores(outputs: dict) -> dict:
    """The recorded metrics as LangSmith feedback; metrics that did not apply are skipped."""
    metrics = outputs["metrics"]
    return {
        "results": [
            {"key": name, "score": value} for name, value in metrics.items() if value is not None
        ]
    }
