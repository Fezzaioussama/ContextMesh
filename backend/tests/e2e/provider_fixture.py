"""Controllable loopback Responses and Embeddings endpoints used by the real adapters."""

import hashlib
import json
import math
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = "fixture-sensitive-upstream-detail"
WORD = re.compile(r"[a-z0-9]+")
DIMENSIONS = 64


class ProviderFixture:
    """Records requests; reasoning tasks are answered by their structured-output name."""

    def __init__(self):
        self.requests = []
        self.embeddings = []
        self.failures_remaining = 0
        self.lock = threading.Lock()

    def respond(self, payload):
        with self.lock:
            self.requests.append(payload)
            if self.failures_remaining:
                self.failures_remaining -= 1
                return 500, {"error": {"message": SECRET, "type": "server_error"}}
            return 200, response_body(payload)

    def embed(self, payload):
        with self.lock:
            self.embeddings.append(payload)
            return 200, embedding_body(payload["input"])

    def tasks(self, name):
        return [request for request in self.requests if task_name(request) == name]


def task_name(payload):
    return payload["text"]["format"]["name"]


def task_content(payload):
    return json.loads(payload["input"][0]["content"][0]["text"])


def plan(content):
    return {
        "standalone_question": content["question"],
        "source_ids": [source["id"] for source in content["sources"]],
        "queries": [content["question"]],
    }


def answer(content):
    passage = content["passages"][0]
    return {
        "status": "answered",
        "claims": [{"text": passage["text"].split("\n")[0], "evidence_ids": [passage["id"]]}],
        "gaps": [],
    }


TASKS = {
    "search_plan": plan,
    "evidence_assessment": lambda content: {
        "sufficient": True,
        "gaps": [],
        "queries": [],
        "expand_source_ids": [],
    },
    "grounded_answer": answer,
    "claim_support": lambda content: {
        "verdicts": [{"index": claim["index"], "supported": True} for claim in content["claims"]]
    },
}


def response_body(payload):
    text = json.dumps(TASKS[task_name(payload)](task_content(payload)))
    return {
        "id": "resp_fixture",
        "object": "response",
        "created_at": 1780488000,
        "status": "completed",
        "model": payload["model"],
        "output": [assistant_output(text)],
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


def assistant_output(text):
    return {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def embedding_body(texts):
    data = [
        {"object": "embedding", "index": index, "embedding": hashed_vector(text)}
        for index, text in enumerate(texts)
    ]
    return {
        "object": "list",
        "data": data,
        "model": "fixture-embedding",
        "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
    }


def hashed_vector(text):
    values = [0.0] * DIMENSIONS
    for word in WORD.findall(text.lower()):
        values[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
    return normalized(values)


def normalized(values):
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


ROUTES = {"/v1/responses": ProviderFixture.respond, "/v1/embeddings": ProviderFixture.embed}


def handler_type(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            route = ROUTES.get(self.path)
            if route is None:
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, body = route(fixture, payload)
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
