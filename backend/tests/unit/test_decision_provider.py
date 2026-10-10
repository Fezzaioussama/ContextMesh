"""OpenRouter decision requests are typed, bounded, credential-isolated, and safe."""

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import httpx
import pytest
from app.data.llm.decisions import OpenRouterDecisionModel
from app.services.ports.decisions import ChoiceTask
from app.services.rules.models import Usage
from app.utils.exceptions import ContextMeshError

TASK = ChoiceTask(
    name="knowledge_route",
    state={"question": "What is in the indexed policy?"},
    instructions="Decide whether this question needs indexed knowledge.",
    criteria={
        "retrieve": "The answer depends on indexed sources.",
        "direct": "The answer does not need indexed sources.",
    },
    timeout_seconds=7.5,
)


def decision_body(**answer):
    choice = {
        "type": "choice",
        "choice": "retrieve",
        "probabilities": {"retrieve": 0.91, "direct": 0.09},
        "confidence": 0.82,
    }
    choice.update(answer)
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {"knowledge_route": choice},
        "usage": {"input_tokens": 287, "output_tokens": 20, "cost": 0.00001},
    }


class RecordingProvider:
    def __init__(self, status_code=200, body=None):
        self.requests = []
        self.status_code = status_code
        self.body = decision_body() if body is None else body

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.body)


class TrickleProvider(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self):
        self.__class__.requests += 1
        content = json.dumps(decision_body()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if self.__class__.requests > 1:
            self.wfile.write(content)
            return
        self._trickle(content)

    def _trickle(self, content):
        try:
            for byte in content:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def model_with(handler, *, api_key="router-only-key", enabled=True):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenRouterDecisionModel(
        enabled=enabled,
        api_key=api_key,
        model="typesafe/jev-1.13",
        endpoint="https://openrouter.test/api/alpha/decisions",
        timeout=45,
        client=client,
    )


def test_choice_request_and_reply_follow_the_decisions_contract():
    provider = RecordingProvider()
    model = model_with(provider)
    reply = model.choose(TASK)
    assert_choice_request(provider.requests[0])
    assert (reply.choice, reply.probabilities, reply.confidence, reply.usage) == (
        "retrieve",
        {"retrieve": 0.91, "direct": 0.09},
        0.82,
        Usage(287, 20),
    )
    model.close()


def assert_choice_request(request):
    assert json.loads(request.content) == {
        "model": "typesafe/jev-1.13",
        "state": {"question": "What is in the indexed policy?"},
        "questions": {
            "knowledge_route": {
                "type": "choice",
                "instructions": "Decide whether this question needs indexed knowledge.",
                "criteria": {
                    "retrieve": "The answer depends on indexed sources.",
                    "direct": "The answer does not need indexed sources.",
                },
            }
        },
    }
    assert request.headers["Authorization"] == "Bearer router-only-key"
    assert request.extensions["timeout"]["read"] == TASK.timeout_seconds


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_http_errors_are_safe_and_not_retried(status_code):
    provider = RecordingProvider(status_code, {"error": {"message": "private response"}})
    model = model_with(provider)
    with pytest.raises(ContextMeshError) as failure:
        model.choose(TASK)
    assert len(provider.requests) == 1
    assert (failure.value.code, failure.value.message, failure.value.retryable) == (
        "provider_unavailable",
        "The model provider is unavailable. Retry later.",
        True,
    )
    model.close()


def test_transport_errors_are_safe_and_not_retried():
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private transport details", request=request)

    model = model_with(timeout)
    with pytest.raises(ContextMeshError) as failure:
        model.choose(TASK)
    assert (len(calls), failure.value.code) == (1, "provider_unavailable")
    model.close()


def test_timed_out_call_does_not_close_the_adapter_or_retry():
    TrickleProvider.requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), TrickleProvider)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model = OpenRouterDecisionModel(
        enabled=True,
        api_key="router-key",
        model="typesafe/jev-1.13",
        endpoint=f"http://127.0.0.1:{server.server_port}/decisions",
        timeout=1,
    )
    started = time.monotonic()
    try:
        with pytest.raises(ContextMeshError) as failure:
            model.choose(replace(TASK, timeout_seconds=0.15))
        elapsed = time.monotonic() - started
        assert (failure.value.code, TrickleProvider.requests) == ("provider_unavailable", 1)
        assert elapsed < 1
        recovered = model.choose(replace(TASK, timeout_seconds=1))
        assert (recovered.choice, TrickleProvider.requests) == ("retrieve", 2)
    finally:
        model.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answers": {"knowledge_route": {}}, "usage": {}},
        decision_body(type="noul"),
        decision_body(choice="unknown"),
        decision_body(probabilities={"retrieve": 1.0}),
        decision_body(probabilities={"retrieve": 0.8, "direct": 0.8}),
        decision_body(confidence=1.5),
        {**decision_body(), "usage": {"input_tokens": -1, "output_tokens": 0}},
    ],
)
def test_malformed_typed_responses_are_safe_invalid_output(body):
    model = model_with(RecordingProvider(body=body))
    with pytest.raises(ContextMeshError) as failure:
        model.choose(TASK)
    assert (failure.value.code, failure.value.retryable) == ("model_output_invalid", True)
    model.close()


def test_non_json_response_is_safe_invalid_output():
    def invalid_json(request):
        return httpx.Response(200, content=b"private non-json response")

    model = model_with(invalid_json)
    with pytest.raises(ContextMeshError) as failure:
        model.choose(TASK)
    assert failure.value.code == "model_output_invalid"
    model.close()


@pytest.mark.parametrize(("enabled", "api_key"), [(False, "router-key"), (True, "")])
def test_disabled_or_uncredentialed_adapter_never_calls_provider(enabled, api_key):
    provider = RecordingProvider()
    model = model_with(provider, enabled=enabled, api_key=api_key)
    with pytest.raises(ContextMeshError) as failure:
        model.choose(TASK)
    assert (model.configured, provider.requests, failure.value.code) == (
        False,
        [],
        "provider_not_configured",
    )
    model.close()


def test_close_releases_the_http_client():
    client = Mock(spec=httpx.Client)
    model = OpenRouterDecisionModel(
        enabled=True,
        api_key="router-key",
        model="typesafe/jev-1.13",
        endpoint="https://openrouter.test/api/alpha/decisions",
        timeout=45,
        client=client,
    )
    model.close()
    client.close.assert_called_once_with()
