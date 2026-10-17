"""The LLM judge: claim faithfulness and answer correctness.

Faithfulness asks whether each claim is supported by the passages it cites; correctness
compares the answer with the case's reference answer. Each metric is one structured model
call. A provider error, a malformed reply, or any other failure is recorded on that metric
and leaves the other intact, so one bad judgement never aborts a run.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial

from app.services.agent.prompts import (
    JUDGE_CORRECTNESS,
    JUDGE_CORRECTNESS_SCHEMA,
    JUDGE_FAITHFULNESS,
    JUDGE_FAITHFULNESS_SCHEMA,
)
from app.services.agent.state import objects
from app.services.evaluation.cases import EvalCase
from app.services.models import AgentOutcome, Claim, ReasoningModel, StructuredTask, Usage
from app.utils.errors import ContextMeshError, failure_code

JUDGE_METRICS = ("faithfulness", "correctness")
TOP_GRADE = 3  # Correctness grades run from 0 (wrong) to 3 (complete and correct).
MAX_VERDICTS = 100  # Far above the agent's claim limit; bounds what a reply can hold.


@dataclass(frozen=True)
class JudgeRequest:
    """One judge call and how to turn its reply into a 0 to 1 score.

    `score` raises ContextMeshError("model_output_invalid") for a malformed reply.
    """

    task: StructuredTask
    score: Callable[[Mapping[str, object]], float]


@dataclass(frozen=True)
class Judgement:
    """The judge's scores for one run.

    `errors` holds the error code of each metric it could not score. `usage` is what the
    judge spent, which never counts toward the agent's token budget.
    """

    scores: dict[str, float | None]
    errors: dict[str, str]
    usage: Usage


class Judge:
    """Grades agent outcomes with a model that should differ from the agent's."""

    def __init__(self, model: ReasoningModel, timeout_seconds: float):
        self._model = model
        self._timeout_seconds = timeout_seconds

    def judged(self, case: EvalCase, outcome: AgentOutcome) -> Judgement:
        """Score faithfulness and correctness; a metric that does not apply is None."""
        requests = {
            "faithfulness": self._faithfulness(outcome),
            "correctness": self._correctness(case, outcome),
        }
        scores: dict[str, float | None] = dict.fromkeys(requests)
        errors: dict[str, str] = {}
        usage = Usage(0, 0)
        for metric, request in requests.items():
            if request is None:
                continue
            try:
                reply = self._model.complete(request.task)
                usage += reply.usage
                scores[metric] = request.score(reply.data)
            except Exception as error:  # A judge failure is recorded and never ends the run.
                errors[metric] = failure_code(error)
        return Judgement(scores, errors, usage)

    def _faithfulness(self, outcome: AgentOutcome) -> JudgeRequest | None:
        """Ask whether each claim's cited passages support it; None without claims.

        A citation is judged on the full passage the answer was drafted from, or on its
        snippet when the outcome carries no context.
        """
        answer = outcome.answer
        if not answer.claims:
            return None
        texts = {item.passage.chunk_id: item.passage.text for item in outcome.context}
        cited = {item.id: texts.get(item.chunk_id, item.snippet) for item in answer.citations}
        content = {
            "claims": [
                {
                    "id": claim.id,
                    "text": claim.text,
                    "passages": [cited[citation_id] for citation_id in claim.citation_ids],
                }
                for claim in answer.claims
            ]
        }
        task = self._task(
            "judge_faithfulness", JUDGE_FAITHFULNESS, content, JUDGE_FAITHFULNESS_SCHEMA
        )
        return JudgeRequest(task, partial(supported_share, answer.claims))

    def _correctness(self, case: EvalCase, outcome: AgentOutcome) -> JudgeRequest | None:
        """Ask how well the answer matches the reference answer; None without a reference."""
        if not case.reference_answer:
            return None
        content = {
            "question": case.question,
            "conversation": [asdict(turn) for turn in case.history],
            "reference_answer": case.reference_answer,
            "answer": outcome.answer.text,
        }
        task = self._task("judge_correctness", JUDGE_CORRECTNESS, content, JUDGE_CORRECTNESS_SCHEMA)
        return JudgeRequest(task, normalized_grade)

    def _task(
        self,
        name: str,
        instructions: str,
        content: Mapping[str, object],
        schema: Mapping[str, object],
    ) -> StructuredTask:
        """A judge call with the shared timeout; content is sent as JSON, like the agent's."""
        payload = json.dumps(content, ensure_ascii=False)
        return StructuredTask(name, instructions, payload, schema, self._timeout_seconds)


def supported_share(claims: tuple[Claim, ...], data: Mapping[str, object]) -> float:
    """The share of claims judged supported.

    Raises:
        ContextMeshError: `model_output_invalid` when any claim lacks a yes-or-no verdict;
            a missing verdict is a judge failure, not an unsupported claim.
    """
    verdicts = {
        item["claim_id"]: item.get("supported")
        for item in objects(data, "verdicts", MAX_VERDICTS)
        if isinstance(item.get("claim_id"), str)
    }
    if not all(isinstance(verdicts.get(claim.id), bool) for claim in claims):
        raise ContextMeshError("model_output_invalid")
    return sum(verdicts[claim.id] for claim in claims) / len(claims)


def normalized_grade(data: Mapping[str, object]) -> float:
    """The correctness grade scaled to 0 to 1.

    Raises:
        ContextMeshError: `model_output_invalid` when the grade is not an integer from 0 to 3.
    """
    grade = data.get("grade")
    if isinstance(grade, bool) or not isinstance(grade, int) or not 0 <= grade <= TOP_GRADE:
        raise ContextMeshError("model_output_invalid")
    return grade / TOP_GRADE
