"""OpenAI/OpenRouter Responses adapter; no tools, stored state, or SDK retries."""

from openai import OpenAI, OpenAIError

from app.ai.prompts.system import INSTRUCTIONS
from app.domain.errors import (
    provider_not_configured,
    provider_unavailable,
)
from app.domain.models import Message, ModelReply, Usage


def user_input(content: str) -> dict:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": content}],
    }


def history_input(item: Message) -> dict:
    if item.role == "user":
        return user_input(item.content)
    return {
        "id": f"msg_{item.id.hex}",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": item.content, "annotations": []}],
    }


class OpenAIChatModel:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        max_output_tokens: int,
        client: OpenAI | None = None,
    ):
        self.model = model
        self.max_output_tokens = max_output_tokens
        self._configured = bool(api_key.strip() and model.strip())
        self.client = client
        if self._configured and client is None:
            self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    @property
    def configured(self) -> bool:
        return self._configured

    def respond(self, history: tuple[Message, ...], message: str) -> ModelReply:
        if not self.configured:
            raise provider_not_configured()
        try:
            response = self._request(history, message)
        except (OpenAIError, ValueError):
            raise provider_unavailable() from None
        return self._reply(response)

    def _request(self, history, message):
        inputs = [history_input(item) for item in history]
        inputs.append(user_input(message))
        return self.client.responses.create(
            model=self.model,
            instructions=INSTRUCTIONS,
            input=inputs,
            store=False,
            max_output_tokens=self.max_output_tokens,
        )

    def _reply(self, response) -> ModelReply:
        if response.status != "completed":
            raise provider_unavailable()
        content = response.output_text.strip()
        if not content:
            raise provider_unavailable()
        if response.usage is None:
            raise provider_unavailable()
        return ModelReply(content, Usage(response.usage.input_tokens, response.usage.output_tokens))

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
