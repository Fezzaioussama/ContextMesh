"""Use the actual SDK against a deterministic HTTP transport, without credentials."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from app.data.llm.embeddings import OpenAIEmbeddingModel
from app.data.llm.openai import OpenAIReasoningModel
from app.services.ports.models import StructuredTask
from app.services.rules.models import Usage
from app.utils.exceptions import ContextMeshError
from openai import OpenAI

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
TASK = StructuredTask("probe", "Return JSON.", '{"question": "q"}', SCHEMA, 12.5)


def response_body(status="completed", content='{"ok": true}'):
    return {
        "id": "resp_fixture",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": "fixture-model",
        "output": [
            {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


class RecordingProvider:
    def __init__(self, status_code=200, body=None):
        self.requests = []
        self.timeouts = []
        self.status_code = status_code
        self.body = response_body() if body is None else body

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        self.timeouts.append(request.extensions["timeout"])
        return httpx.Response(self.status_code, json=self.body)


def sdk(handler):
    return OpenAI(
        api_key="test-only",
        base_url="http://provider.test/v1",
        max_retries=0,
        timeout=45,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def reasoning_with(handler):
    return OpenAIReasoningModel(
        api_key="test-only",
        model="fixture-model",
        base_url="http://provider.test/v1",
        timeout=45,
        max_output_tokens=2048,
        client=sdk(handler),
    )


def test_structured_request_is_strict_stateless_tool_free_and_task_bounded():
    provider = RecordingProvider()
    model = reasoning_with(provider)
    reply = model.complete(TASK)
    request = provider.requests[0]
    assert request["text"]["format"] == {
        "type": "json_schema",
        "name": "probe",
        "schema": SCHEMA,
        "strict": True,
    }
    sent = (
        request["store"],
        request["max_output_tokens"],
        request.get("tools"),
        request["input"][0]["content"][0]["text"],
        provider.timeouts[0]["read"],
    )
    assert sent == (False, 2048, None, '{"question": "q"}', 12.5)
    assert (reply.data, reply.usage) == ({"ok": True}, Usage(10, 4))
    model.close()


def test_fenced_json_is_accepted():
    model = reasoning_with(
        RecordingProvider(body=response_body(content='```json\n{"ok": true}\n```'))
    )
    assert model.complete(TASK).data == {"ok": True}
    model.close()


@pytest.mark.parametrize("content", ["not json", "[1, 2]", ""])
def test_non_object_output_is_a_safe_invalid_model_output(content):
    model = reasoning_with(RecordingProvider(body=response_body(content=content)))
    with pytest.raises(ContextMeshError) as failure:
        model.complete(TASK)
    assert failure.value.code == "model_output_invalid"
    model.close()


def test_incomplete_response_is_a_provider_failure():
    model = reasoning_with(RecordingProvider(body=response_body("incomplete")))
    with pytest.raises(ContextMeshError) as failure:
        model.complete(TASK)
    assert failure.value.code == "provider_unavailable"
    model.close()


def test_output_budget_exhaustion_names_the_setting_to_raise():
    body = {**response_body("incomplete"), "incomplete_details": {"reason": "max_output_tokens"}}
    model = reasoning_with(RecordingProvider(body=body))
    with pytest.raises(ContextMeshError) as failure:
        model.complete(TASK)
    assert failure.value.code == "model_output_limit"
    assert "CONTEXTMESH_MAX_OUTPUT_TOKENS" in failure.value.message
    model.close()


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_sdk_does_not_retry_or_leak_provider_errors(status_code):
    provider = RecordingProvider(status_code, {"error": {"message": "secret prompt/key fixture"}})
    model = reasoning_with(provider)
    with pytest.raises(ContextMeshError) as error:
        model.complete(TASK)
    assert len(provider.requests) == 1
    assert (error.value.code, error.value.message) == (
        "provider_unavailable",
        "The model provider is unavailable. Retry later.",
    )
    model.close()


def test_timeout_is_safe_and_not_retried():
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private transport details", request=request)

    model = reasoning_with(timeout)
    with pytest.raises(ContextMeshError) as error:
        model.complete(TASK)
    assert (len(calls), error.value.code) == (1, "provider_unavailable")
    model.close()


def test_missing_provider_never_constructs_sdk_or_falls_back():
    model = OpenAIReasoningModel(
        api_key="", model="gpt-4.1-mini", base_url="http://unused", timeout=45, max_output_tokens=1
    )
    with pytest.raises(ContextMeshError) as error:
        model.complete(TASK)
    assert model.client is None
    assert error.value.code == "provider_not_configured"


def embedding_body(*vectors):
    data = [
        {"object": "embedding", "index": index, "embedding": list(vector)}
        for index, vector in enumerate(vectors)
    ]
    data.reverse()
    return {
        "object": "list",
        "data": data,
        "model": "fixture",
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }


def embeddings_with(handler):
    return OpenAIEmbeddingModel(
        provider="openrouter",
        api_key="test-only",
        model="vendor/embed",
        base_url="http://provider.test/v1",
        timeout=45,
        client=sdk(handler),
    )


def test_embeddings_are_returned_in_input_order_with_model_identity():
    provider = RecordingProvider(body=embedding_body((1.0, 0.0), (0.0, 1.0)))
    model = embeddings_with(provider)
    assert model.embed(["first", "second"]) == ((1.0, 0.0), (0.0, 1.0))
    sent = (provider.requests[0]["model"], provider.requests[0]["input"], model.identity)
    assert sent == ("vendor/embed", ["first", "second"], "openrouter:vendor/embed")
    model.close()


def test_embedding_count_mismatch_or_error_is_a_safe_failure():
    short = embeddings_with(RecordingProvider(body=embedding_body((1.0,))))
    failing = embeddings_with(RecordingProvider(500, {"error": {"message": "private"}}))
    for model in (short, failing):
        with pytest.raises(ContextMeshError) as error:
            model.embed(["a", "b"])
        assert error.value.code == "provider_unavailable"
        model.close()


def test_rejected_embedding_input_is_permanent_not_retryable():
    model = embeddings_with(RecordingProvider(400, {"error": {"message": "input too long"}}))
    with pytest.raises(ContextMeshError) as error:
        model.embed(["oversized"])
    assert (error.value.code, error.value.retryable) == ("embedding_input_rejected", False)
    model.close()


def test_embeddings_without_credentials_are_not_configured():
    model = OpenAIEmbeddingModel(
        provider="openai", api_key="", model="m", base_url="http://unused", timeout=1
    )
    with pytest.raises(ContextMeshError) as error:
        model.embed(["text"])
    assert error.value.code == "provider_not_configured"


class TricklingProvider(BaseHTTPRequestHandler):
    """Sends keep-alive whitespace slowly, as some gateways do, before any JSON."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(response_body()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body) + 20))
        self.end_headers()
        for _ in range(20):
            if not _trickle(self.wfile):
                return
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _trickle(stream) -> bool:
    try:
        stream.write(b" ")
        stream.flush()
    except OSError:
        return False
    time.sleep(0.1)
    return True


def test_total_call_time_is_bounded_even_when_bytes_keep_arriving():
    server = ThreadingHTTPServer(("127.0.0.1", 0), TricklingProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    model = OpenAIReasoningModel(
        api_key="k",
        model="m",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        timeout=45,
        max_output_tokens=10,
    )
    started = time.monotonic()
    with pytest.raises(ContextMeshError) as error:
        model.complete(StructuredTask("probe", "Return JSON.", "{}", SCHEMA, 0.5))
    elapsed = time.monotonic() - started
    model.close()
    server.shutdown()
    assert (error.value.code, elapsed < 1.5) == ("provider_unavailable", True)


def test_a_stream_closed_by_the_wall_clock_limit_is_a_provider_failure(monkeypatch):
    model = OpenAIReasoningModel(
        api_key="k", model="m", base_url="http://unused/v1", timeout=1, max_output_tokens=10
    )

    def closed_before_reading(task):
        raise httpx.StreamClosed()

    monkeypatch.setattr(model, "_request", closed_before_reading)
    with pytest.raises(ContextMeshError) as error:
        model.complete(StructuredTask("probe", "Return JSON.", "{}", SCHEMA, 1))
    model.close()
    assert error.value.code == "provider_unavailable"
