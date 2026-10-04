"""One bounded model call, with persisted execution claims and replay-safe completion."""

from uuid import UUID

from app.core.exceptions import ContextMeshError
from app.core.security import Identity
from app.domain.errors import provider_not_configured
from app.domain.models import Execution, TurnResult
from app.domain.validation import checked_key, normalized_message
from app.services.ports.conversations import TurnStore
from app.services.ports.models import ChatModel


class Assistant:
    def __init__(self, store: TurnStore, model: ChatModel):
        self._store = store
        self._model = model

    @property
    def configured(self) -> bool:
        return self._model.configured

    def send(self, identity: Identity, conversation_id: UUID, key: str, message: str) -> TurnResult:
        text = normalized_message(message)
        checked_key(key)
        claimed = self._store.claim(identity, conversation_id, key, text)
        if isinstance(claimed, TurnResult):
            return claimed
        return self._execute(identity, claimed)

    def _execute(self, identity: Identity, execution: Execution) -> TurnResult:
        try:
            if not self.configured:
                raise provider_not_configured()
            reply = self._model.respond(execution.history, execution.user_message.content)
        except ContextMeshError:
            self._store.fail(identity, execution)
            raise
        return self._store.complete(identity, execution, reply)
