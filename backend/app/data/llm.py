"""Model clients, all on OpenRouter: structured answers and embeddings through its
OpenAI-compatible API, and its Decisions API for the route classifier.

No client retries on its own, every call has a wall-clock bound, and provider errors
become safe ContextMeshError codes.
"""

import json
import re
import threading
from collections.abc import Sequence
from math import isclose, isfinite

import httpx
from openai import BadRequestError, OpenAI, OpenAIError

from app.services.models import ChoiceReply, ChoiceTask, StructuredReply, StructuredTask, Usage
from app.utils.errors import ContextMeshError

FENCED = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
PROBABILITY_SUM_TOLERANCE = 1e-6  # Allows only ordinary response float-rounding drift.


class OpenRouterReasoningModel:
    """Responses API with a strict JSON schema; no tools."""

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
            raise ContextMeshError("provider_not_configured")
        arguments = dict(
            model=self.model,
            instructions=task.instructions,
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": task.content}],
                }
            ],
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
        try:
            with self.client.responses.with_streaming_response.create(**arguments) as raw:
                response = _within(task.timeout_seconds, raw)
        except (OpenAIError, ValueError, httpx.HTTPError, httpx.StreamError):
            raise ContextMeshError("provider_unavailable") from None
        return StructuredReply(_data(response), _usage(response))

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
    if response.status != "completed":
        # Reasoning models spend hidden tokens; a truncated reply is a budget problem.
        details = getattr(response, "incomplete_details", None)
        if details is not None and details.reason == "max_output_tokens":
            raise ContextMeshError("model_output_limit")
        raise ContextMeshError("provider_unavailable")
    text = response.output_text.strip()
    fenced = FENCED.match(text)
    try:
        value = json.loads(fenced.group(1) if fenced else text)
    except json.JSONDecodeError:
        raise ContextMeshError("model_output_invalid") from None
    if not isinstance(value, dict):
        raise ContextMeshError("model_output_invalid")
    return value


def _usage(response) -> Usage:
    if response.usage is None:
        raise ContextMeshError("provider_unavailable")
    return Usage(response.usage.input_tokens, response.usage.output_tokens)


class OpenRouterEmbeddingModel:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        client: OpenAI | None = None,
    ):
        self.model = model
        self._identity = f"openrouter:{model}"
        self.client = client
        if client is None and api_key.strip() and model.strip():
            self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    @property
    def identity(self) -> str:
        """Names the vector space; each identity gets its own vector collection."""
        return self._identity

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if self.client is None:
            raise ContextMeshError("provider_not_configured")
        try:
            response = self.client.embeddings.create(
                model=self.model, input=list(texts), encoding_format="float"
            )
        except BadRequestError:
            raise ContextMeshError("embedding_input_rejected") from None
        except (OpenAIError, ValueError):
            raise ContextMeshError("provider_unavailable") from None
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(texts):
            raise ContextMeshError("provider_unavailable")
        return tuple(tuple(item.embedding) for item in ordered)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


class OpenRouterDecisionModel:
    """One typed choice with per-option probabilities; any malformed reply is rejected."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str,
        model: str,
        endpoint: str,
        timeout: float,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.endpoint = endpoint
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._configured = enabled and all(map(str.strip, (api_key, model, endpoint)))
        self.client = client

    @property
    def configured(self) -> bool:
        return self._configured

    def choose(self, task: ChoiceTask) -> ChoiceReply:
        if not self.configured:
            raise ContextMeshError("provider_not_configured")
        try:
            if self.client is not None:
                response = self._post(self.client, task, None)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = self._post(client, task, client)
        except (httpx.HTTPError, RuntimeError, TypeError, ValueError):
            raise ContextMeshError("provider_unavailable") from None
        try:
            payload = response.json()
        except ValueError:
            raise ContextMeshError("model_output_invalid") from None
        return _choice_reply(payload, task)

    def _post(
        self, client: httpx.Client, task: ChoiceTask, closable: httpx.Client | None
    ) -> httpx.Response:
        """Before a response exists the deadline can only close `closable`, our own client."""
        question = {
            "type": "choice",
            "instructions": task.instructions,
            "criteria": dict(task.criteria),
        }
        body = {"model": self.model, "state": dict(task.state), "questions": {task.name: question}}
        with _Deadline(task.timeout_seconds, closable) as deadline:
            with client.stream(
                "POST",
                self.endpoint,
                headers=self._headers,
                json=body,
                timeout=task.timeout_seconds,
            ) as response:
                deadline.watch(response)
                response.raise_for_status()
                response.read()
                if deadline.expired:
                    raise ContextMeshError("provider_unavailable")
                return response

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


class _Deadline:
    """Close the active response when per-read timeouts cannot bound total wall time."""

    def __init__(self, seconds: float, target: httpx.Client | None):
        self._lock = threading.Lock()
        self._target: httpx.Client | httpx.Response | None = target
        self._expired = False
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True

    def __enter__(self) -> "_Deadline":
        self._timer.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._timer.cancel()

    def watch(self, response: httpx.Response) -> None:
        with self._lock:
            self._target = response
            expired = self._expired
        if expired:
            response.close()

    @property
    def expired(self) -> bool:
        with self._lock:
            return self._expired

    def _expire(self) -> None:
        with self._lock:
            self._expired = True
            target = self._target
        if target is None:
            return
        try:
            target.close()
        except Exception:
            pass  # The request thread observes the provider failure.


def _choice_reply(payload: object, task: ChoiceTask) -> ChoiceReply:
    invalid = ContextMeshError("model_output_invalid")
    try:
        answer = payload["answers"][task.name]
        kind, choice = answer["type"], answer["choice"]
        probabilities = {name: _unit(value) for name, value in answer["probabilities"].items()}
        confidence = _unit(answer["confidence"])
        usage = Usage(
            _count(payload["usage"]["input_tokens"]), _count(payload["usage"]["output_tokens"])
        )
    except (KeyError, TypeError, AttributeError):
        raise invalid from None
    valid = (
        kind == "choice"
        and isinstance(choice, str)
        and choice in task.criteria
        and set(probabilities) == set(task.criteria)
        and isclose(sum(probabilities.values()), 1.0, rel_tol=0, abs_tol=PROBABILITY_SUM_TOLERANCE)
    )
    if not valid:
        raise invalid
    return ChoiceReply(choice, probabilities, confidence, usage)


def _unit(value: object) -> float:
    """A finite probability in [0, 1]; booleans are not numbers here."""
    number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if number is None or not isfinite(number) or not 0 <= number <= 1:
        raise ContextMeshError("model_output_invalid")
    return float(number)


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContextMeshError("model_output_invalid")
    return value
