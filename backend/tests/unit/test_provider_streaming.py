"""The actual SDK decodes provider SSE; only the direct JSON answer is public."""

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from app.utils.errors import ContextMeshError
from app.utils.streaming import event_scope
from test_provider import TASK, reasoning_with, response_body


class StreamBody(httpx.SyncByteStream):
    def __init__(self, events, observed):
        self.events = events
        self.observed = observed
        self.closed = False
        self.early_delta = False

    def __iter__(self):
        for event in self.events:
            if event["type"] == "response.completed":
                self.early_delta = any(item.event == "delta" for item in self.observed)
            encoded = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            # Split HTTP bytes inside UTF-8, JSON escapes, and SSE delimiters.
            for offset in range(0, len(encoded), 3):
                yield encoded[offset : offset + 3]

    def close(self):
        self.closed = True


def provider(events, observed):
    body = StreamBody(events, observed)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=body)

    return reasoning_with(respond), requests, body


def delta(text):
    return {
        "type": "response.output_text.delta",
        "delta": text,
        "output_index": 0,
        "content_index": 0,
    }


def terminal(text):
    return {"type": "response.completed", "response": response_body(content=text)}


@pytest.mark.parametrize("escaped", [True, False])
def test_direct_answer_stream_decodes_incremental_markdown_and_unicode_before_terminal(escaped):
    answer = '# Café 🙂\n\n**Hello** "reader"\n\n```py\nprint("✓")\n```'
    encoded = json.dumps({"answer": answer}, ensure_ascii=escaped)
    events = [delta(encoded[index : index + 2]) for index in range(0, len(encoded), 2)]
    observed = []
    model, requests, body = provider([*events, terminal(encoded)], observed)
    with event_scope(observed.append):
        reply = model.complete(replace(TASK, name="direct_answer"))
    model.close()
    assert reply.data == {"answer": answer}
    assert "".join(item.data["text"] for item in observed) == answer
    assert requests[0]["stream"] is True
    assert requests[0]["text"]["format"]["strict"] is True
    assert body.closed
    assert body.early_delta


@pytest.mark.parametrize(
    ("tail", "code"),
    [
        ([], "provider_unavailable"),
        ([{"type": "error", "message": "secret"}], "provider_unavailable"),
        ([terminal('{"answer": null}')], "model_output_invalid"),
        ([terminal('{"answer": "Different"}')], "model_output_invalid"),
        ([terminal("not JSON")], "model_output_invalid"),
        (
            [{"type": "response.failed", "response": response_body("failed")}],
            "provider_unavailable",
        ),
        (
            [
                {
                    "type": "response.incomplete",
                    "response": {
                        **response_body("incomplete"),
                        "incomplete_details": {"reason": "max_output_tokens"},
                    },
                }
            ],
            "model_output_limit",
        ),
    ],
)
def test_partial_direct_text_cannot_turn_provider_failure_into_success(tail, code):
    observed = []
    model, requests, body = provider([delta('{"answer":"Hello'), *tail], observed)
    with event_scope(observed.append), pytest.raises(ContextMeshError) as error:
        model.complete(replace(TASK, name="direct_answer"))
    model.close()
    assert error.value.code == code
    assert "secret" not in error.value.message
    assert len(requests) == 1
    assert body.closed


def test_internal_reasoning_and_title_never_request_streaming_or_publish_deltas():
    from test_provider import RecordingProvider

    observed = []
    upstream = RecordingProvider()
    model = reasoning_with(upstream)
    with event_scope(observed.append):
        model.complete(TASK)
        model.complete(replace(TASK, name="conversation_title"))
    model.close()
    assert observed == []
    assert all("stream" not in request for request in upstream.requests)


def test_no_live_subscriber_keeps_direct_json_transport_unchanged():
    from test_provider import RecordingProvider

    upstream = RecordingProvider(body=response_body(content='{"answer":"Hello"}'))
    model = reasoning_with(upstream)
    assert model.complete(replace(TASK, name="direct_answer")).data == {"answer": "Hello"}
    assert "stream" not in upstream.requests[0]
    model.close()


@pytest.mark.parametrize("initial", [[], [delta('{"answer":"Hello')]])
def test_final_provider_answer_supplies_any_text_missing_from_delta_events(initial):
    observed = []
    model, _, body = provider([*initial, terminal('{"answer":"Hello **world**"}')], observed)
    with event_scope(observed.append):
        reply = model.complete(replace(TASK, name="direct_answer"))
    assert "".join(item.data["text"] for item in observed) == "Hello **world**"
    assert reply.data["answer"] == "Hello **world**"
    model.close()
    assert body.closed


def test_streamed_text_uses_the_same_trim_and_length_limit_as_saved_markdown():
    from app.services.agent.state import markdown_field

    answer = "  \n# Title\n\n```py\n    x = 1\n```\n" + "x" * 8100 + "\n "
    encoded = json.dumps({"answer": answer})
    observed = []
    model, _, _ = provider([delta(encoded), terminal(encoded)], observed)
    with event_scope(observed.append):
        model.complete(replace(TASK, name="direct_answer"))
    assert "".join(item.data["text"] for item in observed) == markdown_field(
        {"answer": answer}, "answer", 8000
    )
    model.close()


@pytest.mark.parametrize("bad", [None, False, 7, ["secret"], {"text": "secret"}])
def test_non_string_answer_never_leaks_raw_json(bad):
    observed = []
    encoded = json.dumps({"answer": bad})
    model, _, _ = provider([delta(encoded), terminal(encoded)], observed)
    with event_scope(observed.append), pytest.raises(ContextMeshError) as error:
        model.complete(replace(TASK, name="direct_answer"))
    assert observed == []
    assert error.value.code == "model_output_invalid"
    model.close()


class TricklingEvents(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(f"data: {json.dumps(delta('{"answer":"Hello'))}\n\n".encode())
            self.wfile.flush()
            for _ in range(30):
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                time.sleep(0.05)
        except OSError:
            return

    def log_message(self, *args):
        pass


def test_stream_deadline_closes_a_trickling_provider_without_terminal_success():
    from app.data.llm import OpenRouterReasoningModel

    server = ThreadingHTTPServer(("127.0.0.1", 0), TricklingEvents)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model = OpenRouterReasoningModel(
        api_key="fixture",
        model="fixture",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        timeout=5,
        max_output_tokens=100,
    )
    observed = []
    started = time.monotonic()
    try:
        with event_scope(observed.append), pytest.raises(ContextMeshError) as error:
            model.complete(replace(TASK, name="direct_answer", timeout_seconds=0.2))
        assert error.value.code == "provider_unavailable"
        assert time.monotonic() - started < 1
        assert any(item.event == "delta" for item in observed)
    finally:
        model.close()
        server.shutdown()
        server.server_close()
        thread.join()
