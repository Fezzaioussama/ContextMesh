"""The one place that picks concrete classes: the external clients, then the API's
services and the worker's job handlers. Tests substitute `ExternalServices`."""

import os
import socket
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from functools import partial
from hashlib import sha256
from uuid import uuid4

from qdrant_client import QdrantClient
from sqlalchemy import Engine

from app.data.blobs import FilesystemBlobStore
from app.data.chat import ChatRepository
from app.data.db import create_database_engine, database_ready, provision_identity
from app.data.fetcher import SafeHttpFetcher
from app.data.indexing import CrawlRepository, IndexingRepository
from app.data.jobs import JobRepository
from app.data.llm import (
    OpenRouterDecisionModel,
    OpenRouterEmbeddingModel,
    OpenRouterReasoningModel,
    OpenRouterRerankModel,
)
from app.data.qdrant import QdrantVectorIndex
from app.data.retrieval import RetrievalRepository
from app.data.sources import SourceRepository
from app.data.tracing import (
    DisabledTracing,
    LangSmithTracing,
    TracedDecisionModel,
    TracedEmbeddingModel,
    TracedReasoningModel,
    TracedRerankModel,
    TracedSearch,
)
from app.services.agent.graph import AgentWorkflow
from app.services.agent.state import AgentPolicy
from app.services.agent.steps import Agent
from app.services.chat import ConversationService
from app.services.crawler import CrawlPolicy, SiteCrawler
from app.services.indexing import DocumentIndexer, Eraser, IngestionWorker
from app.services.models import (
    DecisionModel,
    EmbeddingModel,
    Identity,
    ReasoningModel,
    RerankModel,
)
from app.services.retrieval import HybridRetriever
from app.services.sources import SourceService
from app.utils.config import Settings

# A failed rerank falls back to term-coverage ranking, so a slow one should not hold up
# the turn for the full provider timeout.
RERANK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ExternalServices:
    reasoning: ReasoningModel
    embeddings: EmbeddingModel
    vectors: QdrantVectorIndex
    fetcher: SafeHttpFetcher
    decisions: DecisionModel | None = None
    reranker: RerankModel | None = None


def collection_name(embedding_identity: str) -> str:
    """Each embedding model gets its own collection, so dimensions never mix."""
    return f"contextmesh_{sha256(embedding_identity.encode()).hexdigest()[:16]}"


def create_services(config: Settings, resources: ExitStack) -> ExternalServices:
    provider = config.selected_model
    decision = config.selected_decision
    api_key = provider.api_key.get_secret_value()
    reasoning = OpenRouterReasoningModel(
        api_key=api_key,
        model=provider.model,
        base_url=provider.base_url,
        timeout=config.provider_timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )
    resources.callback(reasoning.close)
    embeddings = OpenRouterEmbeddingModel(
        api_key=api_key,
        model=provider.embedding_model,
        base_url=provider.base_url,
        timeout=config.provider_timeout_seconds,
    )
    resources.callback(embeddings.close)
    reranker = OpenRouterRerankModel(
        api_key=api_key,
        model=provider.rerank_model,
        base_url=provider.base_url,
        timeout=min(config.provider_timeout_seconds, RERANK_TIMEOUT_SECONDS),
    )
    resources.callback(reranker.close)
    decisions = OpenRouterDecisionModel(
        enabled=decision.enabled,
        api_key=decision.api_key.get_secret_value(),
        model=decision.model,
        endpoint=decision.endpoint,
        timeout=config.provider_timeout_seconds,
    )
    resources.callback(decisions.close)
    client = QdrantClient(url=config.qdrant_url, timeout=5, check_compatibility=False)
    resources.callback(client.close)
    vectors = QdrantVectorIndex(client, collection_name(embeddings.identity))
    fetcher = SafeHttpFetcher(
        timeout_seconds=config.web_timeout_seconds, max_bytes=config.web_max_page_bytes
    )
    resources.callback(fetcher.close)
    return ExternalServices(reasoning, embeddings, vectors, fetcher, decisions, reranker)


# API


@dataclass(frozen=True)
class Api:
    """What the HTTP layer serves."""

    conversations: ConversationService
    sources: SourceService
    database_ready: Callable[[], bool]
    provision: Callable[[Identity], None]


def build_api(config: Settings, services: ExternalServices | None, resources: ExitStack) -> Api:
    """Open the database engine and compose the API's services. `resources` closes what
    this creates; injected `services` stay owned by the caller."""
    engine = create_database_engine(config.database_url)
    resources.callback(engine.dispose)
    if services is None:
        services = create_services(config, resources)
    tracing = create_tracing(config, resources)
    services = traced_services(services, config)
    policy = AgentPolicy(
        deadline_seconds=config.agent_deadline_seconds,
        call_timeout_seconds=config.provider_timeout_seconds,
    )
    retrieval = RetrievalRepository(engine)
    retriever = traced_search(
        HybridRetriever(services.embeddings, services.vectors, retrieval, services.reranker),
        config,
    )
    agent = Agent(services.reasoning, retriever, services.decisions, policy)
    provider = config.selected_model
    conversations = ConversationService(
        ChatRepository(engine, config.turn_lease_seconds),
        retrieval,
        AgentWorkflow(agent, tracing),
        services.reasoning,
        provider=provider.provider,
        model_name=provider.model,
        embedding_model=provider.embedding_model,
        max_output_tokens=config.max_output_tokens,
        policy=policy,
    )
    sources = SourceService(
        SourceRepository(engine),
        retrieval,
        FilesystemBlobStore(config.blob_dir),
        max_upload_bytes=config.max_upload_bytes,
    )
    return Api(
        conversations,
        sources,
        partial(database_ready, engine),
        partial(provision_identity, engine),
    )


def create_tracing(config: Settings, resources: ExitStack) -> LangSmithTracing | DisabledTracing:
    """Only the API traces; the ingestion worker never creates a LangSmith client."""
    selected = config.selected_tracing
    if not selected.enabled:
        return DisabledTracing()
    tracing = LangSmithTracing(
        api_key=selected.api_key.get_secret_value(),
        project=selected.project,
        endpoint=selected.endpoint,
    )
    resources.callback(tracing.close)
    return tracing


def traced_services(services: ExternalServices, config: Settings) -> ExternalServices:
    """The agent's model clients, reporting a span per call when tracing is on."""
    if not config.selected_tracing.enabled:
        return services
    provider = config.selected_model
    decisions = services.decisions
    if decisions is not None:
        decisions = TracedDecisionModel(decisions, model=config.selected_decision.model)
    reranker = services.reranker
    if reranker is not None:
        reranker = TracedRerankModel(reranker, model=provider.rerank_model)
    return replace(
        services,
        reasoning=TracedReasoningModel(
            services.reasoning, provider=provider.provider, model=provider.model
        ),
        embeddings=TracedEmbeddingModel(services.embeddings),
        decisions=decisions,
        reranker=reranker,
    )


def traced_search(search: HybridRetriever, config: Settings) -> HybridRetriever:
    return TracedSearch(search) if config.selected_tracing.enabled else search


# Worker


def build_worker(engine: Engine, config: Settings, services: ExternalServices) -> IngestionWorker:
    jobs = JobRepository(engine, config.job_lease_seconds)
    store = IndexingRepository(engine)
    blobs = FilesystemBlobStore(config.blob_dir)
    eraser = Eraser(jobs, store, blobs, services.vectors)
    handlers = {
        "document.index_requested": DocumentIndexer(
            jobs, store, blobs, services.embeddings, services.vectors
        ),
        "source.sync_requested": SiteCrawler(
            jobs,
            CrawlRepository(engine),
            services.fetcher,
            blobs,
            CrawlPolicy(config.crawl_max_pages, config.crawl_max_depth),
        ),
        "document.delete_requested": eraser,
        "source.delete_requested": eraser,
    }
    worker_id = f"{socket.gethostname()[:60]}:{os.getpid()}:{uuid4().hex[:8]}"
    return IngestionWorker(jobs, handlers, worker_id)
