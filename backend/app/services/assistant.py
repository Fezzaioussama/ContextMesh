"""Grounded turns: authorize scope, claim the turn, run the bounded agent, release."""

from uuid import UUID

from app.services.ports.conversations import TurnStore
from app.services.ports.models import ReasoningModel
from app.services.ports.retrieval import AnswerWorkflow, SourceCatalog
from app.services.rules.errors import provider_not_configured
from app.services.rules.knowledge import CatalogSource
from app.services.rules.models import AgentOutcome, Execution, TurnInput, TurnResult
from app.services.rules.validation import checked_key, checked_source_filter, normalized_message
from app.utils.exceptions import ContextMeshError
from app.utils.logging import flow_event
from app.utils.security import Identity


class Assistant:
    def __init__(
        self,
        store: TurnStore,
        catalog: SourceCatalog,
        workflow: AnswerWorkflow,
        model: ReasoningModel,
    ):
        self._store = store
        self._catalog = catalog
        self._workflow = workflow
        self._model = model

    @property
    def configured(self) -> bool:
        return self._model.configured

    def send(
        self,
        identity: Identity,
        conversation_id: UUID,
        key: str,
        message: str,
        source_ids: tuple[UUID, ...] | None = None,
    ) -> TurnResult:
        turn = TurnInput(normalized_message(message), checked_source_filter(source_ids))
        checked_key(key)
        saved = self._store.replay(identity, conversation_id, key, turn)
        if saved is not None:
            flow_event("ask_question", "replayed", conversation_id=conversation_id)
            return saved
        catalog = self._catalog.eligible(identity, turn.source_ids)
        claimed = self._store.claim(identity, conversation_id, key, turn)
        if isinstance(claimed, TurnResult):
            return claimed
        return self._execute(identity, claimed, catalog)

    def _execute(
        self, identity: Identity, execution: Execution, catalog: tuple[CatalogSource, ...]
    ) -> TurnResult:
        flow_event("ask_question", "turn_started", turn_id=execution.turn_id)
        try:
            outcome = self._answer(identity, execution, catalog)
            result = self._store.complete(identity, execution, outcome)
        except ContextMeshError as error:
            self._failed(identity, execution, error.code)
            raise
        except Exception:
            self._failed(identity, execution, "internal_error")
            raise
        flow_event(
            "ask_question",
            "answered",
            turn_id=execution.turn_id,
            status=outcome.answer.status,
            citations=len(outcome.answer.citations),
            tokens=outcome.usage.total,
        )
        return result

    def _failed(self, identity: Identity, execution: Execution, code: str) -> None:
        self._store.fail(identity, execution, code)
        flow_event("ask_question", "failed", turn_id=execution.turn_id, code=code)

    def _answer(
        self, identity: Identity, execution: Execution, catalog: tuple[CatalogSource, ...]
    ) -> AgentOutcome:
        if not self.configured:
            raise provider_not_configured()
        question = execution.user_message.content
        return self._workflow.run(identity, question, execution.history, catalog)
