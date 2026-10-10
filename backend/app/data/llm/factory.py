"""Construct the supported provider adapters from resolved provider arguments."""

from app.data.llm.decisions import OpenRouterDecisionModel
from app.data.llm.embeddings import OpenAIEmbeddingModel
from app.data.llm.openai import OpenAIReasoningModel


def create_reasoning_model(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: float,
    max_output_tokens: int,
) -> OpenAIReasoningModel:
    return OpenAIReasoningModel(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
    )


def create_embedding_model(
    *, provider: str, api_key: str, model: str, base_url: str, timeout: float
) -> OpenAIEmbeddingModel:
    return OpenAIEmbeddingModel(
        provider=provider, api_key=api_key, model=model, base_url=base_url, timeout=timeout
    )


def create_decision_model(
    *, enabled: bool, api_key: str, model: str, endpoint: str, timeout: float
) -> OpenRouterDecisionModel:
    return OpenRouterDecisionModel(
        enabled=enabled,
        api_key=api_key,
        model=model,
        endpoint=endpoint,
        timeout=timeout,
    )
