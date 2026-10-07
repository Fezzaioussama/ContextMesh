"""Published grounded-answer shapes; citation display fields come from canonical records."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field


class LocatorResponse(BaseModel):
    """`page` is set for PDFs and `slide` for presentations; lines count within them."""

    model_config = ConfigDict(from_attributes=True)
    heading_path: list[str]
    line_start: int
    line_end: int
    page: int | None
    slide: int | None


class CitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    number: int
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_generation_id: UUID
    chunk_id: UUID
    title: str
    locator: LocatorResponse
    snippet: str
    source_url: str | None

    @computed_field
    @property
    def evidence_path(self) -> str:
        return (
            f"/api/v1/documents/{self.document_id}/versions/{self.document_version_id}"
            f"/evidence?chunk_id={self.chunk_id}"
        )


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    text: str
    citation_ids: list[str]


class AnswerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: Literal["answered", "partial", "insufficient_evidence", "withheld"]
    claims: list[ClaimResponse]
    citations: list[CitationResponse]
    gaps: list[str]


class TraceStage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    stage: str
    summary: str
