"""Choose the API's LangSmith tracing: the run scope and traced agent ports.

Only the API composes this; the ingestion worker never creates a LangSmith client.
"""

from contextlib import ExitStack
from dataclasses import replace

from app.data.tracing.models import (
    TracedDecisionModel,
    TracedEmbeddingModel,
    TracedReasoningModel,
)
from app.data.tracing.retrieval import TracedEvidenceSearch
from app.data.tracing.scope import DisabledTracing, LangSmithTracing
from app.services.ports.decisions import DecisionModel
from app.services.ports.retrieval import EvidenceSearch
from app.services.ports.tracing import RunTracing
from app.setup.services import ExternalServices
from app.utils.config import Settings


def create_tracing(config: Settings, resources: ExitStack) -> RunTracing:
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
    """The agent's model ports, reporting a span per call when tracing is on."""
    if not config.selected_tracing.enabled:
        return services
    provider = config.selected_model
    return replace(
        services,
        reasoning=TracedReasoningModel(
            services.reasoning, provider=provider.provider, model=provider.model
        ),
        embeddings=TracedEmbeddingModel(services.embeddings),
        decisions=_traced_decisions(services.decisions, config),
    )


def traced_search(search: EvidenceSearch, config: Settings) -> EvidenceSearch:
    return TracedEvidenceSearch(search) if config.selected_tracing.enabled else search


def _traced_decisions(decisions: DecisionModel | None, config: Settings) -> DecisionModel | None:
    if decisions is None:
        return None
    return TracedDecisionModel(decisions, model=config.selected_decision.model)
