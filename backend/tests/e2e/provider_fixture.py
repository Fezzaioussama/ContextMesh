"""Controllable loopback Responses, Embeddings, and Rerank endpoints used by the real
adapters."""

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
STREAM_ANSWER = (
    "## Live answer\n\n**Streaming works** with Café 🙂.\n\n- First item\n- Second item\n\n"
    "```python\nprint('hello')\n"
    "example_message = 'This intentionally long code line scrolls inside its own keyboard-accessible code block.'\n"
    "```\n\n| Feature | Status | Detail |\n| --- | --- | --- |\n"
    "| Streaming | Ready | Visible before completion |\n"
    "| Markdown | Ready | Headings, code, lists, and tables |"
)


class ProviderFixture:
    """Records requests; reasoning tasks are answered by their structured-output name."""

    def __init__(self):
        self.requests = []
        self.embeddings = []
        self.reranks = []
        self.failures_remaining = 0
        self.lock = threading.Lock()
        self.stream_terminal = threading.Event()
        self.stream_terminal.set()

    def respond(self, payload):
        with self.lock:
            self.requests.append(payload)
            if self.failures_remaining and task_name(payload) == "search_plan":
                self.failures_remaining -= 1
                return 500, {"error": {"message": SECRET, "type": "server_error"}}
            return 200, response_body(payload)

    def embed(self, payload):
        with self.lock:
            self.embeddings.append(payload)
            return 200, embedding_body(payload["input"])

    def rerank(self, payload):
        with self.lock:
            self.reranks.append(payload)
            return 200, rerank_body(payload["query"], payload["documents"])

    def tasks(self, name):
        return [request for request in self.requests if task_name(request) == name]

    def decide(self, payload):
        direct = "streaming Markdown" in payload["state"]["request"]
        route = "direct" if direct else "retrieve"
        probabilities = {"direct": 0.99 if direct else 0.01, "retrieve": 0.01 if direct else 0.99}
        return 200, {
            "answers": {
                "retrieval_route": {
                    "type": "choice",
                    "choice": route,
                    "probabilities": probabilities,
                    "confidence": 0.99,
                }
            },
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }


def task_name(payload):
    return payload["text"]["format"]["name"]


def task_content(payload):
    return json.loads(payload["input"][0]["content"][0]["text"])


def plan(content):
    return {
        "standalone_question": content["question"],
        "source_ids": [source["id"] for source in content["sources"]],
        "questions": [],
        "keywords": [],
        "hypothetical_answer": "",
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
    "passage_rerank": lambda content: {
        "grades": [{"id": passage["id"], "relevance": 3} for passage in content["passages"]]
    },
    "evidence_assessment": lambda content: {
        "sufficient": True,
        "gaps": [],
        "questions": [],
        "keywords": [],
        "expand_source_ids": [],
    },
    "grounded_answer": answer,
    "claim_support": lambda content: {
        "verdicts": [{"index": claim["index"], "supported": True} for claim in content["claims"]]
    },
    "conversation_title": lambda content: {"title": "Fixture conversation"},
    "direct_answer": lambda content: {"answer": STREAM_ANSWER},
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


def rerank_body(query, documents):
    """Scores by the cosine of hashed word vectors, strongest first like the real API."""
    target = hashed_vector(query)
    scores = [sum(a * b for a, b in zip(target, hashed_vector(text))) for text in documents]
    order = sorted(range(len(documents)), key=lambda index: -scores[index])
    results = [
        {"index": index, "relevance_score": scores[index], "document": {"text": documents[index]}}
        for index in order
    ]
    return {"model": "fixture-rerank", "results": results}


def hashed_vector(text):
    values = [0.0] * DIMENSIONS
    for word in WORD.findall(text.lower()):
        values[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
    return normalized(values)


def normalized(values):
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


ROUTES = {
    "/v1/responses": ProviderFixture.respond,
    "/v1/embeddings": ProviderFixture.embed,
    "/v1/rerank": ProviderFixture.rerank,
    "/v1/decisions": ProviderFixture.decide,
}


def handler_type(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            route = ROUTES.get(self.path)
            if route is None:
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, body = route(fixture, payload)
            if payload.get("stream") and status == 200:
                self._stream_response(body)
                return
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _stream_response(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            text = body["output"][0]["content"][0]["text"]
            for offset in range(0, len(text), 12):
                self._event(
                    {
                        "type": "response.output_text.delta",
                        "delta": text[offset : offset + 12],
                        "output_index": 0,
                        "content_index": 0,
                    }
                )
            if not fixture.stream_terminal.wait(timeout=10):
                return
            self._event({"type": "response.completed", "response": body})

        def _event(self, event):
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()

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
