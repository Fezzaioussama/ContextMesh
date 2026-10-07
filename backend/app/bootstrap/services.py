"""Construct external integrations once; callers that inject them keep ownership."""

from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from qdrant_client import QdrantClient

from app.ai.llm.factory import create_embedding_model, create_reasoning_model
from app.core.config import Settings
from app.search.qdrant import QdrantVectorIndex
from app.services.ports.ingestion import VectorWriter
from app.services.ports.models import EmbeddingModel, ReasoningModel
from app.services.ports.retrieval import VectorSearch


class VectorIndex(VectorWriter, VectorSearch, Protocol):
    """The composed adapter serves the worker's writes and the query side's search."""


@dataclass(frozen=True)
class ExternalServices:
    reasoning: ReasoningModel
    embeddings: EmbeddingModel
    vectors: VectorIndex


def collection_name(embedding_identity: str) -> str:
    """Each embedding model gets its own collection, so dimensions never mix."""
    return f"contextmesh_{sha256(embedding_identity.encode()).hexdigest()[:16]}"


def create_services(config: Settings, resources: ExitStack) -> ExternalServices:
    provider = config.selected_model
    api_key = provider.api_key.get_secret_value()
    reasoning = create_reasoning_model(
        api_key=api_key,
        model=provider.model,
        base_url=provider.base_url,
        timeout=config.provider_timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )
    resources.callback(reasoning.close)
    embeddings = create_embedding_model(
        provider=provider.provider,
        api_key=api_key,
        model=provider.embedding_model,
        base_url=provider.base_url,
        timeout=config.provider_timeout_seconds,
    )
    resources.callback(embeddings.close)
    client = QdrantClient(url=config.qdrant_url, timeout=5, check_compatibility=False)
    resources.callback(client.close)
    vectors = QdrantVectorIndex(client, collection_name(embeddings.identity))
    return ExternalServices(reasoning, embeddings, vectors)
