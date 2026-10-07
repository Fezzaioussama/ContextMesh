"""Grounded turns: authorize scope, claim the turn, run the bounded agent, release."""

from uuid import UUID

from app.core.exceptions import ContextMeshError
from app.core.security import Identity
from app.domain.errors import provider_not_configured
from app.domain.knowledge import CatalogSource
from app.domain.models import AgentOutcome, Execution, TurnInput, TurnResult
from app.domain.validation import checked_key, checked_source_filter, normalized_message
from app.services.ports.conversations import TurnStore
from app.services.ports.models import ReasoningModel
from app.services.ports.retrieval import AnswerWorkflow, SourceCatalog


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
            return saved
        catalog = self._catalog.eligible(identity, turn.source_ids)
        claimed = self._store.claim(identity, conversation_id, key, turn)
        if isinstance(claimed, TurnResult):
            return claimed
        return self._execute(identity, claimed, catalog)

    def _execute(
        self, identity: Identity, execution: Execution, catalog: tuple[CatalogSource, ...]
    ) -> TurnResult:
        try:
            return self._store.complete(
                identity, execution, self._answer(identity, execution, catalog)
            )
        except ContextMeshError as error:
            self._store.fail(identity, execution, error.code)
            raise
        except Exception:
            self._store.fail(identity, execution, "internal_error")
            raise

    def _answer(
        self, identity: Identity, execution: Execution, catalog: tuple[CatalogSource, ...]
    ) -> AgentOutcome:
        if not self.configured:
            raise provider_not_configured()
        question = execution.user_message.content
        return self._workflow.run(identity, question, execution.history, catalog)
