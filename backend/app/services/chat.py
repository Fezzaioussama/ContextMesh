"""Conversations and grounded turns: validate, replay or claim the turn, run the
bounded agent with no database transaction open, then save the answer. A conversation's
first question also names it."""

import json
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from uuid import UUID

from app.data.chat import ChatRepository
from app.data.retrieval import RetrievalRepository
from app.services.agent.graph import AgentWorkflow
from app.services.agent.prompts import TITLE, TITLE_SCHEMA
from app.services.agent.state import AgentPolicy
from app.services.chat_stream import SLOTS, ChatStream
from app.services.models import (
    DEFAULT_TITLE,
    CatalogSource,
    Conversation,
    Execution,
    Identity,
    Message,
    Page,
    ReasoningModel,
    StructuredTask,
    TurnInput,
    TurnResult,
)
from app.services.stream_events import progress
from app.services.validation import (
    checked_key,
    checked_page,
    checked_source_filter,
    normalized_message,
    normalized_title,
)
from app.utils.errors import ContextMeshError
from app.utils.logging import flow_event, flow_scope

TITLE_TIMEOUT_SECONDS = 20.0
TITLE_GRACE_SECONDS = 5.0  # How long a finished answer waits for a slower title.
TITLE_CHARS = 60
TITLE_TRIM = " \"'“”‘’«».,:;!?"


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
            execution, catalog = self._prepare(identity, conversation_id, key, message, source_ids)
            if isinstance(execution, TurnResult):
                return execution
            return self._run(identity, execution, catalog)

    def open_stream(
        self,
        identity: Identity,
        conversation_id: UUID,
        key: str,
        message: str,
        source_ids: tuple[UUID, ...] | None = None,
    ) -> ChatStream:
        """Validate and claim before HTTP headers; admit at most eight running streams."""
        if not SLOTS.acquire(blocking=False):
            raise ContextMeshError("turn_in_progress")
        try:
            with flow_scope(conversation_id=conversation_id):
                execution, catalog = self._prepare(
                    identity, conversation_id, key, message, source_ids
                )
                return ChatStream(
                    lambda: (
                        execution
                        if isinstance(execution, TurnResult)
                        else self._run(identity, execution, catalog)
                    )
                )
        except Exception:
            SLOTS.release()
            raise

    def _prepare(
        self,
        identity: Identity,
        conversation_id: UUID,
        key: str,
        message: str,
        source_ids: tuple[UUID, ...] | None,
    ) -> tuple[Execution | TurnResult, tuple[CatalogSource, ...]]:
        with flow_scope(conversation_id=conversation_id):
            try:
                turn = TurnInput(normalized_message(message), checked_source_filter(source_ids))
                checked_key(key)
                saved = self._repository.replay(identity, conversation_id, key, turn)
                if saved is not None:
                    flow_event("ask_question", "replayed")
                    return saved, ()
                catalog = self._catalog.eligible(identity, turn.source_ids)
                execution = self._repository.claim(identity, conversation_id, key, turn)
            except ContextMeshError as error:
                flow_event("ask_question", "rejected", code=error.code)
                raise
            return execution, catalog

    def _run(
        self, identity: Identity, execution: Execution, catalog: tuple[CatalogSource, ...]
    ) -> TurnResult:
        """Run the agent for a claimed turn; any failure releases the claim."""
        with flow_scope(turn_id=execution.turn_id):
            flow_event("ask_question", "turn_started")
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                if not self._model.configured:
                    raise ContextMeshError("provider_not_configured")
                question = execution.user_message.content
                # The first question names the conversation while the agent works.
                title = None if execution.history else pool.submit(self._title, question)
                outcome = self._workflow.run(identity, question, execution.history, catalog)
                progress("persist")
                result = self._repository.complete(identity, execution, outcome)
            except Exception as error:
                code = error.code if isinstance(error, ContextMeshError) else "internal_error"
                self._repository.fail(identity, execution, code)
                flow_event("ask_question", "failed", code=code)
                raise
            finally:
                pool.shutdown(wait=False)
            if title is not None:
                self._repository.rename_untitled(
                    identity, execution.conversation_id, _settled(title, question)
                )
            flow_event(
                "ask_question",
                "answered",
                status=outcome.answer.status,
                citations=len(outcome.answer.citations),
                tokens=outcome.usage.total,
            )
            return result

    def _title(self, question: str) -> str:
        """A short model-written name; the question itself when the model gives none."""
        content = json.dumps({"message": question}, ensure_ascii=False)
        task = StructuredTask(
            "conversation_title", TITLE, content, TITLE_SCHEMA, TITLE_TIMEOUT_SECONDS
        )
        try:
            proposed = self._model.complete(task).data.get("title")
        except ContextMeshError:
            proposed = None
        return short_title(proposed) or short_title(question) or DEFAULT_TITLE


def _settled(title: Future[str], question: str) -> str:
    """The model's title if it arrives within the grace period; the question otherwise."""
    try:
        return title.result(timeout=TITLE_GRACE_SECONDS)
    except TimeoutError:
        return short_title(question) or DEFAULT_TITLE


def short_title(text: object) -> str:
    """One line without wrapping quotes or final punctuation, cut at a word boundary."""
    if not isinstance(text, str):
        return ""
    value = " ".join(text.split()).strip(TITLE_TRIM)
    if len(value) <= TITLE_CHARS:
        return value
    return value[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(TITLE_TRIM) + "…"
