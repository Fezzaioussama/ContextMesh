"""Construct the supported Responses adapter from resolved provider arguments."""

from app.ai.llm.openai import OpenAIChatModel


def create_chat_model(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: float,
    max_output_tokens: int,
) -> OpenAIChatModel:
    return OpenAIChatModel(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
    )
