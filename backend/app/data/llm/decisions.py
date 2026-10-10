"""Raw OpenRouter Decisions API adapter for bounded choice classification."""

from collections.abc import Mapping
from math import isclose, isfinite
from threading import Lock, Timer

import httpx

from app.services.ports.decisions import ChoiceReply, ChoiceTask
from app.services.rules.errors import (
    model_output_invalid,
    provider_not_configured,
    provider_unavailable,
)
from app.services.rules.models import Usage

PROBABILITY_SUM_TOLERANCE = 1e-6  # Allows only ordinary response float-rounding drift.


class OpenRouterDecisionModel:
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
        self._configured = _is_configured(enabled, api_key, model, endpoint)
        self.client = client

    @property
    def configured(self) -> bool:
        return self._configured

    def choose(self, task: ChoiceTask) -> ChoiceReply:
        if not self.configured:
            raise provider_not_configured()
        response = self._post(task)
        return _reply(_json(response), task)

    def _post(self, task: ChoiceTask) -> httpx.Response:
        try:
            if self.client is not None:
                return self._stream(self.client, task, None)
            with httpx.Client(timeout=self._timeout) as client:
                return self._stream(client, task, client)
        except (httpx.HTTPError, RuntimeError, TypeError, ValueError):
            raise provider_unavailable() from None

    def _stream(
        self,
        client: httpx.Client,
        task: ChoiceTask,
        pre_response_target: httpx.Client | None,
    ) -> httpx.Response:
        with _Deadline(task.timeout_seconds, pre_response_target) as deadline:
            with client.stream(
                "POST",
                self.endpoint,
                headers=self._headers,
                json=_request(self.model, task),
                timeout=task.timeout_seconds,
            ) as response:
                deadline.watch(response)
                response.raise_for_status()
                response.read()
                if deadline.expired:
                    raise provider_unavailable()
                return response

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


class _Deadline:
    """Close the active response when per-read timeouts cannot bound total wall time."""

    def __init__(self, seconds: float, target: httpx.Client | None):
        self._lock = Lock()
        self._target: httpx.Client | httpx.Response | None = target
        self._expired = False
        self._timer = Timer(seconds, self._expire)
        self._timer.daemon = True

    def __enter__(self) -> "_Deadline":
        self._timer.start()
        return self

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
        if target is not None:
            _close_safely(target)

    def __exit__(self, *args: object) -> None:
        self._timer.cancel()


def _close_safely(target: httpx.Client | httpx.Response) -> None:
    try:
        target.close()
    except Exception:
        pass  # The request thread observes the provider failure.


def _request(model: str, task: ChoiceTask) -> dict:
    question = {
        "type": "choice",
        "instructions": task.instructions,
        "criteria": dict(task.criteria),
    }
    return {"model": model, "state": dict(task.state), "questions": {task.name: question}}


def _is_configured(enabled: bool, api_key: str, model: str, endpoint: str) -> bool:
    if not enabled:
        return False
    return all(map(str.strip, (api_key, model, endpoint)))


def _json(response: httpx.Response) -> Mapping[str, object]:
    try:
        return _mapping(response.json())
    except ValueError:
        raise model_output_invalid() from None


def _reply(payload: Mapping[str, object], task: ChoiceTask) -> ChoiceReply:
    try:
        answer = _mapping(_mapping(payload["answers"])[task.name])
        _choice_type(answer["type"])
        choice = _choice(answer["choice"], task.criteria)
        probabilities = _probabilities(answer["probabilities"], task.criteria)
        confidence = _unit_float(answer["confidence"])
        usage = _usage(payload["usage"])
    except (KeyError, TypeError):
        raise model_output_invalid() from None
    return ChoiceReply(choice, probabilities, confidence, usage)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise model_output_invalid()
    return value


def _choice(value: object, criteria: Mapping[str, str]) -> str:
    if not isinstance(value, str):
        raise model_output_invalid()
    if value not in criteria:
        raise model_output_invalid()
    return value


def _choice_type(value: object) -> None:
    if value != "choice":
        raise model_output_invalid()


def _probabilities(value: object, criteria: Mapping[str, str]) -> Mapping[str, float]:
    values = _mapping(value)
    if set(values) != set(criteria):
        raise model_output_invalid()
    probabilities = {name: _unit_float(item) for name, item in values.items()}
    return _normalized(probabilities)


def _normalized(probabilities: Mapping[str, float]) -> Mapping[str, float]:
    if not isclose(
        sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=PROBABILITY_SUM_TOLERANCE
    ):
        raise model_output_invalid()
    return probabilities


def _unit_float(value: object) -> float:
    if isinstance(value, bool):
        raise model_output_invalid()
    if not isinstance(value, (int, float)):
        raise model_output_invalid()
    return _bounded(float(value))


def _bounded(number: float) -> float:
    if not isfinite(number):
        raise model_output_invalid()
    if number < 0:
        raise model_output_invalid()
    if number > 1:
        raise model_output_invalid()
    return number


def _usage(value: object) -> Usage:
    values = _mapping(value)
    try:
        return Usage(_token_count(values["input_tokens"]), _token_count(values["output_tokens"]))
    except KeyError:
        raise model_output_invalid() from None


def _token_count(value: object) -> int:
    if isinstance(value, bool):
        raise model_output_invalid()
    if not isinstance(value, int):
        raise model_output_invalid()
    if value < 0:
        raise model_output_invalid()
    return value
