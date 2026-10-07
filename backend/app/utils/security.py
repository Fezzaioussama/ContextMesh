"""Server-derived principal shared by application modules."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Identity:
    subject: str
    workspace_id: UUID
