"""Controllable loopback Responses endpoint used by the real provider adapter."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "Fixture response: <script>window.fixtureExecuted = true</script> <b>literal</b>"
SECRET = "fixture-sensitive-upstream-detail"


class ProviderFixture:
    def __init__(self):
        self.requests = []
        self.failures_remaining = 0
        self.lock = threading.Lock()

    def respond(self, payload):
        with self.lock:
            self.requests.append(payload)
            if self.failures_remaining:
                self.failures_remaining -= 1
                return 500, {"error": {"message": SECRET, "type": "server_error"}}
            return 200, response_body()


def response_body():
    return {
        "id": "resp_fixture",
        "object": "response",
        "created_at": 1780488000,
        "status": "completed",
        "model": "gpt-4.1-mini",
        "output": [assistant_output()],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 12,
            "output_tokens": 8,
            "total_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def assistant_output():
    return {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": REPLY, "annotations": []}],
    }


def handler_type(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/responses":
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, body = fixture.respond(payload)
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    return Handler


@contextmanager
def provider_server():
    fixture = ProviderFixture()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type(fixture))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fixture, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
