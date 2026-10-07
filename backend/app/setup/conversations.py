"""Wire the grounded conversation module: retrieval, agent steps, and LangGraph."""

import time

from sqlalchemy import Engine

from app.data.db.repositories.conversation_repository import (
    ConversationRepository,
)
from app.data.db.repositories.retrieval_repository import RetrievalRepository
from app.data.db.repositories.turn_repository import TurnRepository
from app.services.agent.assessor import EvidenceAssessor
from app.services.agent.budget import BudgetedModel
from app.services.agent.checker import SupportChecker
from app.services.agent.gather import EvidenceGatherer
from app.services.agent.graph import LangGraphWorkflow
from app.services.agent.planner import SearchPlanner
from app.services.agent.policy import AgentPolicy
from app.services.agent.steps import AgentSteps
from app.services.agent.writer import AnswerWriter
from app.services.assistant import Assistant
from app.services.chat_service import ConversationService
from app.services.retrieval import HybridRetriever, TermOverlapReranker
from app.setup.services import ExternalServices
from app.utils.config import Settings


def agent_policy(config: Settings) -> AgentPolicy:
    return AgentPolicy(
        deadline_seconds=config.agent_deadline_seconds,
        call_timeout_seconds=config.provider_timeout_seconds,
    )


def agent_steps(engine: Engine, services: ExternalServices, policy: AgentPolicy) -> AgentSteps:
    repository = RetrievalRepository(engine)
    retriever = HybridRetriever(
        services.embeddings, services.vectors, repository, repository, TermOverlapReranker()
    )
    model = BudgetedModel(services.reasoning, policy, time.monotonic)
    return AgentSteps(
        model,
        SearchPlanner(model, policy),
        EvidenceGatherer(retriever, model, policy),
        EvidenceAssessor(model, policy),
        AnswerWriter(model, policy),
        SupportChecker(model),
        policy,
    )


def conversation_service(
    engine: Engine, config: Settings, services: ExternalServices
) -> ConversationService:
    provider = config.selected_model
    policy = agent_policy(config)
    workflow = LangGraphWorkflow(agent_steps(engine, services, policy))
    assistant = Assistant(
        TurnRepository(engine, config.turn_lease_seconds),
        RetrievalRepository(engine),
        workflow,
        services.reasoning,
    )
    return ConversationService(
        ConversationRepository(engine),
        assistant,
        provider=provider.provider,
        model_name=provider.model,
        embedding_model=provider.embedding_model,
        max_output_tokens=config.max_output_tokens,
        policy=policy,
    )
