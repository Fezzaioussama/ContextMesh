"""The values every layer shares: who is asking, sources and documents, jobs, passages,
answers, conversations, and the model clients the agent calls."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class Identity:
    """The server-derived principal; clients can never choose it."""

    subject: str
    workspace_id: UUID


# Sources, documents, and jobs

SourceKind = Literal["upload", "website"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class JobView:
    id: UUID
    kind: str
    status: JobStatus
    attempts: int
    stage: str | None
    error_code: str | None
    updated_at: datetime


@dataclass(frozen=True)
class Source:
    id: UUID
    kind: SourceKind
    name: str
    description: str
    document_count: int
    searchable_count: int
    created_at: datetime
    url: str | None = None
    latest_sync: JobView | None = None


@dataclass(frozen=True)
class CatalogSource:
    """A source as the agent sees it when choosing where to search."""

    id: UUID
    name: str
    description: str
    searchable_count: int


@dataclass(frozen=True)
class DocumentSummary:
    id: UUID
    source_id: UUID
    title: str
    media_type: str
    searchable: bool
    chunk_count: int
    latest_job: JobView | None
    updated_at: datetime
    uri: str | None = None


@dataclass(frozen=True)
class UploadReceipt:
    document: DocumentSummary
    job_id: UUID
    duplicate: bool


@dataclass(frozen=True)
class UploadSpec:
    """A document to version: an uploaded file, or a crawled page identified by URL."""

    title: str
    external_id: str
    media_type: str
    byte_size: int
    uri: str | None = None


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    kind: str
    workspace_id: UUID
    source_id: UUID
    document_id: UUID | None
    document_version_id: UUID | None
    token: int
    attempts: int
    max_attempts: int


@dataclass(frozen=True)
class IndexTarget:
    workspace_id: UUID
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    blob_key: str
    media_type: str
    title: str


@dataclass(frozen=True)
class WebsiteTarget:
    workspace_id: UUID
    source_id: UUID
    start_url: str


@dataclass(frozen=True)
class FetchedPage:
    url: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class PageSummary:
    title: str | None
    links: tuple[str, ...]


# Parsed text, chunks, and passages


@dataclass(frozen=True)
class Locator:
    """Lines count within the extracted text of the page or slide when those are set."""

    heading_path: tuple[str, ...]
    line_start: int
    line_end: int
    page: int | None = None
    slide: int | None = None


@dataclass(frozen=True)
class Element:
    """One parsed block of text and where it came from."""

    text: str
    heading_path: tuple[str, ...]
    line_start: int
    line_end: int
    page: int | None = None
    slide: int | None = None


@dataclass(frozen=True)
class ChunkDraft:
    id: UUID
    ordinal: int
    text: str
    locator: Locator
    token_count: int

    @property
    def embedding_text(self) -> str:
        if not self.locator.heading_path:
            return self.text
        return f"{' > '.join(self.locator.heading_path)}\n\n{self.text}"


@dataclass(frozen=True)
class VectorPoint:
    chunk_id: UUID
    vector: tuple[float, ...]
    workspace_id: UUID
    source_id: UUID
    document_id: UUID
    generation_id: UUID


@dataclass(frozen=True)
class Passage:
    chunk_id: UUID
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_generation_id: UUID
    source_name: str
    title: str
    locator: Locator
    text: str
    uri: str | None = None


@dataclass(frozen=True)
class Evidence:
    passage: Passage
    score: float
    round: int


@dataclass(frozen=True)
class RetrievalResult:
    evidence: tuple[Evidence, ...]
    candidates: int
    vector_available: bool


# Answers

AnswerStatus = Literal["direct", "answered", "partial", "insufficient_evidence", "withheld"]
WITHHELD_TEXT = "This answer is unavailable because evidence it relied on was removed."


@dataclass(frozen=True)
class Citation:
    """Every field comes from the canonical evidence record, never from model output."""

    id: str
    number: int
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_generation_id: UUID
    chunk_id: UUID
    title: str
    locator: Locator
    snippet: str
    source_url: str | None = None


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    status: AnswerStatus
    text: str
    claims: tuple[Claim, ...]
    citations: tuple[Citation, ...]
    gaps: tuple[str, ...]


def withheld_answer() -> Answer:
    return Answer("withheld", WITHHELD_TEXT, (), (), ())


@dataclass(frozen=True)
class TraceStage:
    stage: str
    summary: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(frozen=True)
class AgentOutcome:
    """`consulted` lists every document whose passages a model saw for this answer."""

    answer: Answer
    trace: tuple[TraceStage, ...]
    usage: Usage
    consulted: tuple[UUID, ...] = ()


# Conversations


@dataclass(frozen=True)
class Conversation:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Message:
    id: UUID
    turn_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    answer: Answer | None = None
    trace: tuple[TraceStage, ...] = ()


@dataclass(frozen=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class TurnInput:
    message: str
    source_ids: tuple[UUID, ...] | None = None

    @property
    def fingerprint(self) -> str:
        """Unfiltered turns keep the original message-only hash for replay compatibility."""
        payload = self.message
        if self.source_ids is not None:
            scope = ",".join(sorted(str(item) for item in self.source_ids))
            payload = f"{self.message}\x00{scope}"
        return sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Execution:
    """A claimed turn: only the holder of `token` may complete or fail it."""

    turn_id: UUID
    conversation_id: UUID
    token: UUID
    user_message: Message
    history: tuple[Message, ...]


@dataclass(frozen=True)
class TurnResult:
    turn_id: UUID
    conversation_id: UUID
    user_message: Message
    assistant_message: Message
    usage: Usage
    trace: tuple[TraceStage, ...] = ()


# Model clients. Each has a real adapter, a LangSmith-traced wrapper, and test doubles.


@dataclass(frozen=True)
class StructuredTask:
    name: str
    instructions: str
    content: str
    schema: Mapping[str, object]
    timeout_seconds: float


@dataclass(frozen=True)
class StructuredReply:
    data: Mapping[str, object]
    usage: Usage


@dataclass(frozen=True)
class ChoiceTask:
    name: str
    state: Mapping[str, object]
    instructions: str
    criteria: Mapping[str, str]
    timeout_seconds: float


@dataclass(frozen=True)
class ChoiceReply:
    choice: str
    probabilities: Mapping[str, float]
    confidence: float
    usage: Usage


class ReasoningModel(Protocol):
    @property
    def configured(self) -> bool: ...

    def complete(self, task: StructuredTask) -> StructuredReply: ...


class EmbeddingModel(Protocol):
    @property
    def identity(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class DecisionModel(Protocol):
    @property
    def configured(self) -> bool: ...

    def choose(self, task: ChoiceTask) -> ChoiceReply: ...
