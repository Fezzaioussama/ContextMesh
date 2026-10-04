"""Real process and HTTP lifecycle helpers for the initial-chat smoke run."""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request(base, path, body=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Idempotency-Key"] = key
    encoded = encode_body(body)
    operation = urllib.request.Request(base + path, data=encoded, headers=headers)
    try:
        with urllib.request.urlopen(operation, timeout=60) as response:
            return response.status, json.load(response), response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.load(error), error.headers


def encode_body(body):
    if body is None:
        return None
    return json.dumps(body).encode()


def wait_ready(url, process):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        require(process.poll() is None, f"Process exited while waiting for {url}")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise TimeoutError(f"Runtime did not become ready: {url}")


def stop_process(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


@contextmanager
def started(command, cwd, environment, log_path):
    with log_path.open("a") as log:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=log, stderr=log)
        try:
            yield process
        finally:
            stop_process(process)


def api_environment(settings, provider_url, configured=True):
    environment = os.environ.copy()
    environment.update(
        CONTEXTMESH_DATABASE_URL=settings.database_url,
        CONTEXTMESH_DEV_SUBJECT=settings.subject,
        CONTEXTMESH_MODEL_PROVIDER=settings.model_provider,
        OPENAI_API_KEY="fixture-key",
        OPENAI_MODEL="gpt-4.1-mini",
        OPENAI_BASE_URL=provider_url,
        OPENROUTER_API_KEY="router-fixture-key",
        OPENROUTER_MODEL="openai/gpt-4.1-mini",
        OPENROUTER_BASE_URL=provider_url,
        CONTEXTMESH_ALLOWED_ORIGINS=json.dumps([settings.frontend_url]),
    )
    if not configured:
        environment["OPENAI_API_KEY"] = ""
        environment["OPENROUTER_API_KEY"] = ""
    return environment


def api_command(settings):
    return [
        "uv",
        "run",
        "--project",
        "backend",
        "--locked",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.api_port),
    ]


@contextmanager
def running_api(settings, provider_url, log_dir, configured=True):
    environment = api_environment(settings, provider_url, configured)
    with started(api_command(settings), ROOT, environment, log_dir / "api.log") as process:
        wait_ready(settings.api_url + "/health/ready", process)
        yield


@contextmanager
def running_frontend(settings, log_dir):
    environment = os.environ.copy()
    environment["CONTEXTMESH_API_PROXY_TARGET"] = settings.api_url
    command = [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.frontend_port),
        "--strictPort",
    ]
    with started(command, ROOT / "frontend", environment, log_dir / "frontend.log") as process:
        wait_ready(settings.frontend_url, process)
        yield
