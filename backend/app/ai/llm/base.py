"""Public AI namespace for the application-owned model contracts."""

from app.services.ports.models import EmbeddingModel, ReasoningModel

__all__ = ["EmbeddingModel", "ReasoningModel"]
