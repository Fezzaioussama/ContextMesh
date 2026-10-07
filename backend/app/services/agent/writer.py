"""Draft cited claims from bounded context; references must name supplied passages."""

from collections.abc import Mapping
from dataclasses import replace

from app.domain.knowledge import Evidence
from app.services.agent.budget import BudgetedModel
from app.services.agent.context import labeled, passages_payload, selected_context
from app.services.agent.instructions import ANSWER, ANSWER_SCHEMA
from app.services.agent.output import objects, string_list, text_field
from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState, Draft, DraftClaim
from app.services.agent.wording import counted

STATUSES = frozenset({"answered", "partial", "insufficient_evidence"})


class AnswerWriter:
    def __init__(self, model: BudgetedModel, policy: AgentPolicy):
        self._model = model
        self._policy = policy

    def draft(self, state: AgentState) -> AgentState:
        context = selected_context(state.evidence, self._policy)
        if not context:
            summary = "No passage passed access checks, so no answer was drafted."
            return state.traced("answer", summary, draft=None)
        return self._write(state, context, (), "answer")

    def repair(self, state: AgentState, draft: Draft) -> AgentState:
        rejected = tuple(draft.claims[index].text for index in state.unsupported)
        revised = self._write(state, draft.context, rejected, "repair")
        return replace(revised, repairs=state.repairs + 1, unsupported=())

    def _write(
        self,
        state: AgentState,
        context: tuple[Evidence, ...],
        rejected: tuple[str, ...],
        stage: str,
    ) -> AgentState:
        content = _content(state.question, context, rejected)
        reply = self._model.call(state, "grounded_answer", ANSWER, content, ANSWER_SCHEMA)
        draft = self._parsed(reply.data, context)
        summary = (
            f"Drafted {counted(len(draft.claims), 'cited claim')} from "
            f"{counted(len(context), 'passage')}."
        )
        return state.charged(reply.usage).traced(stage, summary, draft=draft)

    def _parsed(self, data: Mapping[str, object], context: tuple[Evidence, ...]) -> Draft:
        labels = labeled(context)
        items = objects(data, "claims", self._policy.max_claims)
        claims = tuple(_valid_claims(items, labels))
        gaps = string_list(data, "gaps", self._policy.max_gaps, 300)
        return Draft(_status(data), claims, gaps, context)


def _content(
    question: str, context: tuple[Evidence, ...], rejected: tuple[str, ...]
) -> dict[str, object]:
    content: dict[str, object] = {"question": question, "passages": passages_payload(context)}
    if rejected:
        content["rejected_claims"] = list(rejected)
    return content


def _valid_claims(
    items: tuple[Mapping[str, object], ...], labels: dict[str, Evidence]
) -> list[DraftClaim]:
    claims = [_claim(item, labels) for item in items]
    return [claim for claim in claims if claim is not None]


def _claim(item: Mapping[str, object], labels: dict[str, Evidence]) -> DraftClaim | None:
    """Drop claims whose references are absent from the supplied context."""
    text = text_field(item, "text", 1000)
    cited = tuple(dict.fromkeys(_cited(item, labels)))
    if not text or not cited:
        return None
    return DraftClaim(text, cited)


def _cited(item: Mapping[str, object], labels: dict[str, Evidence]) -> list[Evidence]:
    keys = [label.upper() for label in string_list(item, "evidence_ids", 8, 16)]
    return [labels[key] for key in keys if key in labels]


def _status(data: Mapping[str, object]) -> str:
    status = text_field(data, "status", 40)
    return status if status in STATUSES else "partial"
