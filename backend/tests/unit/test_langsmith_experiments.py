"""LangSmith dataset sync and experiment replay, against a fake client: no network."""

from types import SimpleNamespace

import langsmith
from app.data.experiments import Experiment, LangSmithExperiments, published_scores


def stored(case_id):
    return SimpleNamespace(inputs={"case_id": case_id, "question": f"{case_id}?"})


class FakeClient:
    """Records dataset calls; `examples` is what LangSmith already holds."""

    def __init__(self, has_dataset, examples=()):
        self.exists = has_dataset
        self.examples = list(examples)
        self.created_dataset = None
        self.created_examples = None
        self.flushed = False

    def has_dataset(self, dataset_name):
        return self.exists

    def read_dataset(self, dataset_name):
        return SimpleNamespace(id="existing-id")

    def create_dataset(self, dataset_name, description):
        self.created_dataset = dataset_name
        return SimpleNamespace(id="new-id")

    def list_examples(self, dataset_id):
        return iter(self.examples)

    def create_examples(self, dataset_id, examples):
        self.created_examples = (dataset_id, examples)
        self.examples += [SimpleNamespace(inputs=example["inputs"]) for example in examples]

    def flush(self):
        self.flushed = True


def example(case_id):
    return {"inputs": {"case_id": case_id}, "outputs": {}, "metadata": {"tags": []}}


def experiments(client):
    return LangSmithExperiments(api_key="ls-key", endpoint="http://unused", client=client)


def test_a_missing_dataset_is_created_with_every_example():
    client = FakeClient(has_dataset=False)
    synced = experiments(client).synced("starter", {"a": example("a"), "b": example("b")})
    assert (client.created_dataset, client.created_examples) == (
        "starter",
        ("new-id", [example("a"), example("b")]),
    )
    assert [item.inputs["case_id"] for item in synced] == ["a", "b"]


def test_re_syncing_adds_only_new_case_ids_and_returns_only_the_selected_ones():
    client = FakeClient(has_dataset=True, examples=[stored("a"), stored("unselected")])
    synced = experiments(client).synced("starter", {"a": example("a"), "b": example("b")})
    assert (client.created_dataset, client.created_examples) == (
        None,
        ("existing-id", [example("b")]),
    )
    assert sorted(item.inputs["case_id"] for item in synced) == ["a", "b"]


def test_nothing_is_created_when_every_case_is_already_there():
    client = FakeClient(has_dataset=True, examples=[stored("a")])
    experiments(client).synced("starter", {"a": example("a")})
    assert client.created_examples is None


def test_an_experiment_replays_each_case_in_order_and_publishes_its_scores(monkeypatch):
    calls = {}

    def fake_evaluate(target, *, data, evaluators, num_repetitions, **options):
        calls["options"] = options
        outputs = [target(item.inputs) for _ in range(num_repetitions) for item in data]
        calls["feedback"] = [evaluator(output) for output in outputs for evaluator in evaluators]
        calls["outputs"] = outputs
        return SimpleNamespace(experiment_name="starter-1a2b")

    # Set in the module dict: reading the lazy original would import the real framework.
    monkeypatch.setitem(vars(langsmith), "evaluate", fake_evaluate)
    runs = {
        "a": [
            {"repeat": 1, "metrics": {"route_correct": True, "faithfulness": None}},
            {"repeat": 2, "metrics": {}},
        ],
        "b": [{"repeat": 1, "metrics": {"correctness": 0.5}}, {"repeat": 2, "metrics": {}}],
    }
    experiment = Experiment(
        "starter", (stored("a"), stored("b")), runs, 2, {"judge": "judge/model"}
    )
    client = FakeClient(has_dataset=True)
    publisher = experiments(client)
    assert publisher.logged(experiment) == "starter-1a2b"
    assert [output["repeat"] for output in calls["outputs"]] == [1, 1, 2, 2]
    assert calls["feedback"][:2] == [
        {"results": [{"key": "route_correct", "score": True}]},
        {"results": [{"key": "correctness", "score": 0.5}]},
    ]
    assert (calls["options"]["max_concurrency"], calls["options"]["experiment_prefix"]) == (
        0,
        "starter",
    )
    publisher.close()
    assert client.flushed


def test_scores_that_did_not_apply_are_not_published():
    assert published_scores({"metrics": {"quote_recall": None, "repairs": 0}}) == {
        "results": [{"key": "repairs", "score": 0}]
    }
