"""OpenAI/OpenRouter Responses adapter for strict structured output; no tools or retries."""

import json
import re
import threading

import httpx
from openai import OpenAI, OpenAIError

from app.services.ports.models import StructuredReply, StructuredTask
from app.services.rules.errors import (
    model_output_invalid,
    model_output_limit,
    provider_not_configured,
    provider_unavailable,
)
from app.services.rules.models import Usage

FENCED = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def user_input(content: str) -> dict:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": content}],
    }


class OpenAIReasoningModel:
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

    def complete(self, task: StructuredTask) -> StructuredReply:
        if not self.configured:
            raise provider_not_configured()
        try:
            response = self._request(task)
        except (OpenAIError, ValueError, httpx.HTTPError, httpx.StreamError):
            raise provider_unavailable() from None
        return StructuredReply(_data(response), _usage(response))

    def _request(self, task: StructuredTask):
        with self.client.responses.with_streaming_response.create(**self._arguments(task)) as raw:
            return _within(task.timeout_seconds, raw)

    def _arguments(self, task: StructuredTask) -> dict:
        return dict(
            model=self.model,
            instructions=task.instructions,
            input=[user_input(task.content)],
            store=False,
            max_output_tokens=self.max_output_tokens,
            text={
                "format": {
                    "type": "json_schema",
                    "name": task.name,
                    "schema": dict(task.schema),
                    "strict": True,
                }
            },
            timeout=task.timeout_seconds,
        )

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def _within(seconds: float, raw):
    """Bound the whole call: providers may trickle keep-alive bytes past read timeouts."""
    timer = threading.Timer(seconds, raw.http_response.close)
    timer.daemon = True
    timer.start()
    try:
        return raw.parse()
    finally:
        timer.cancel()


def _data(response) -> dict:
    _require_completed(response)
    try:
        value = json.loads(_unfenced(response.output_text))
    except json.JSONDecodeError:
        raise model_output_invalid() from None
    if not isinstance(value, dict):
        raise model_output_invalid()
    return value


def _require_completed(response) -> None:
    """Reasoning models spend hidden tokens; a truncated reply is a budget problem."""
    if response.status == "completed":
        return
    if _truncated(response):
        raise model_output_limit()
    raise provider_unavailable()


def _truncated(response) -> bool:
    details = getattr(response, "incomplete_details", None)
    return details is not None and details.reason == "max_output_tokens"


def _unfenced(text: str) -> str:
    stripped = text.strip()
    fenced = FENCED.match(stripped)
    return fenced.group(1) if fenced else stripped


def _usage(response) -> Usage:
    if response.usage is None:
        raise provider_unavailable()
    return Usage(response.usage.input_tokens, response.usage.output_tokens)
