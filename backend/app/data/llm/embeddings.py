"""OpenAI-compatible embeddings adapter (OpenAI or OpenRouter); no SDK retries."""

from collections.abc import Sequence

from openai import BadRequestError, OpenAI, OpenAIError

from app.services.rules.errors import (
    embedding_input_rejected,
    provider_not_configured,
    provider_unavailable,
)


class OpenAIEmbeddingModel:
    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        client: OpenAI | None = None,
    ):
        self.model = model
        self._identity = f"{provider}:{model}"
        self.client = client
        if client is None and api_key.strip() and model.strip():
            self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    @property
    def identity(self) -> str:
        return self._identity

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if self.client is None:
            raise provider_not_configured()
        try:
            response = self.client.embeddings.create(
                model=self.model, input=list(texts), encoding_format="float"
            )
        except BadRequestError:
            raise embedding_input_rejected() from None
        except (OpenAIError, ValueError):
            raise provider_unavailable() from None
        return _vectors(response.data, len(texts))

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def _vectors(data: list, expected: int) -> tuple[tuple[float, ...], ...]:
    ordered = sorted(data, key=lambda item: item.index)
    if len(ordered) != expected:
        raise provider_unavailable()
    return tuple(tuple(item.embedding) for item in ordered)
