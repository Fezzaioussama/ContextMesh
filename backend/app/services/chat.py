"""Conversations and grounded turns: validate, replay or claim the turn, run the
bounded agent with no database transaction open, then save the answer."""

from dataclasses import dataclass
from uuid import UUID

from app.data.chat import ChatRepository
from app.data.retrieval import RetrievalRepository
from app.services.agent.graph import AgentWorkflow
from app.services.agent.state import AgentPolicy
from app.services.models import (
    CatalogSource,
    Conversation,
    Execution,
    Identity,
    Message,
    Page,
    ReasoningModel,
    TurnInput,
    TurnResult,
)
from app.services.validation import (
    checked_key,
    checked_page,
    checked_source_filter,
    normalized_message,
    normalized_title,
)
from app.utils.errors import ContextMeshError
from app.utils.logging import flow_event, flow_scope


@dataclass(frozen=True)
class AssistantMetadata:
    provider: str
    model: str
    embedding_model: str
    configured: bool
    max_output_tokens: int
    policy: AgentPolicy


class ConversationService:
    def __init__(
        self,
        repository: ChatRepository,
        catalog: RetrievalRepository,
        workflow: AgentWorkflow,
        model: ReasoningModel,
        *,
        provider: str,
        model_name: str,
        embedding_model: str,
        max_output_tokens: int,
        policy: AgentPolicy,
    ):
        self._repository = repository
        self._catalog = catalog
        self._workflow = workflow
        self._model = model
        self._metadata = (provider, model_name, embedding_model)
        self._max_output_tokens = max_output_tokens
        self._policy = policy

    def metadata(self) -> AssistantMetadata:
        """`configured` is read on every call, so it reflects the provider right now."""
        return AssistantMetadata(
            *self._metadata, self._model.configured, self._max_output_tokens, self._policy
        )

    def create(self, identity: Identity, title: str) -> Conversation:
        return self._repository.create(identity, normalized_title(title))

    def conversations(
        self, identity: Identity, limit: int, cursor: str | None
    ) -> Page[Conversation]:
        checked_page(limit, 50, cursor)
        return self._repository.conversations(identity, limit, cursor)

    def messages(
        self, identity: Identity, conversation_id: UUID, limit: int, cursor: str | None
    ) -> Page[Message]:
        checked_page(limit, 100, cursor)
        return self._repository.messages(identity, conversation_id, limit, cursor)

    def send(
        self,
        identity: Identity,
        conversation_id: UUID,
        key: str,
        message: str,
        source_ids: tuple[UUID, ...] | None = None,
    ) -> TurnResult:
        """Answer one message. Repeating an idempotency key returns the saved answer."""
        with flow_scope(conversation_id=conversation_id):
            try:
                turn = TurnInput(normalized_message(message), checked_source_filter(source_ids))
                checked_key(key)
                saved = self._repository.replay(identity, conversation_id, key, turn)
                if saved is not None:
                    flow_event("ask_question", "replayed")
                    return saved
                catalog = self._catalog.eligible(identity, turn.source_ids)
                execution = self._repository.claim(identity, conversation_id, key, turn)
            except ContextMeshError as error:
                flow_event("ask_question", "rejected", code=error.code)
                raise
            if isinstance(execution, TurnResult):  # Completed concurrently under this key.
                return execution
            return self._run(identity, execution, catalog)

    def _run(
        self, identity: Identity, execution: Execution, catalog: tuple[CatalogSource, ...]
    ) -> TurnResult:
        """Run the agent for a claimed turn; any failure releases the claim."""
        with flow_scope(turn_id=execution.turn_id):
            flow_event("ask_question", "turn_started")
            try:
                if not self._model.configured:
                    raise ContextMeshError("provider_not_configured")
                question = execution.user_message.content
                outcome = self._workflow.run(identity, question, execution.history, catalog)
                result = self._repository.complete(identity, execution, outcome)
            except Exception as error:
                code = error.code if isinstance(error, ContextMeshError) else "internal_error"
                self._repository.fail(identity, execution, code)
                flow_event("ask_question", "failed", code=code)
                raise
            flow_event(
                "ask_question",
                "answered",
                status=outcome.answer.status,
                citations=len(outcome.answer.citations),
                tokens=outcome.usage.total,
            )
            return result
