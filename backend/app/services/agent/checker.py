"""Model-assessed claim support; missing or malformed verdicts fail closed."""

from collections.abc import Mapping

from app.services.agent.budget import BudgetedModel
from app.services.agent.context import labeled
from app.services.agent.instructions import SUPPORT, SUPPORT_SCHEMA
from app.services.agent.output import objects
from app.services.agent.state import AgentState, Draft, DraftClaim


class SupportChecker:
    def __init__(self, model: BudgetedModel):
        self._model = model

    def check(self, state: AgentState, draft: Draft) -> AgentState:
        content = {"claims": _claims_payload(draft)}
        reply = self._model.call(state, "claim_support", SUPPORT, content, SUPPORT_SCHEMA)
        unsupported = unsupported_claims(reply.data, len(draft.claims))
        supported = len(draft.claims) - len(unsupported)
        summary = f"Checked claim support: {supported} supported, {len(unsupported)} unsupported."
        return state.charged(reply.usage).traced("verify", summary, unsupported=unsupported)


def unsupported_claims(data: Mapping[str, object], count: int) -> tuple[int, ...]:
    supported = _supported_indexes(data)
    return tuple(index for index in range(count) if index not in supported)


def _supported_indexes(data: Mapping[str, object]) -> set[object]:
    """Only explicit true verdicts count; anything missing or malformed is unsupported."""
    return {item.get("index") for item in objects(data, "verdicts", 100) if _accepted(item)}


def _accepted(item: Mapping[str, object]) -> bool:
    return item.get("supported") is True


def _claims_payload(draft: Draft) -> list[dict[str, object]]:
    labels = {item: label for label, item in labeled(draft.context).items()}
    return [_claim_payload(index, claim, labels) for index, claim in enumerate(draft.claims)]


def _claim_payload(index: int, claim: DraftClaim, labels: dict) -> dict[str, object]:
    return {
        "index": index,
        "text": claim.text,
        "passages": [{"id": labels[item], "text": item.passage.text} for item in claim.evidence],
    }
