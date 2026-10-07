"""Executable deterministic smoke run for agentic retrieval across real processes."""

import argparse
import os
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from api_checks import (
    index_fixture_document,
    verify_history,
    verify_missing_configuration,
    verify_replay,
    verify_safe_failure,
)
from browser_checks import (
    verify_browser_chat,
    verify_browser_sources,
    verify_mobile,
    verify_ui_failure,
    verify_ui_missing_configuration,
)
from playwright.sync_api import sync_playwright
from provider_fixture import provider_server
from runtime import ROOT, api_environment, running_api, running_frontend, running_worker


@dataclass(frozen=True)
class Settings:
    database_url: str
    qdrant_url: str
    blob_dir: Path
    api_port: int
    frontend_port: int
    subject: str
    workspace_id: str
    model_provider: str

    @property
    def api_url(self):
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def frontend_url(self):
        return f"http://127.0.0.1:{self.frontend_port}"


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    default_url = "postgresql+psycopg://contextmesh:contextmesh@127.0.0.1:55432/contextmesh_test"
    parser.add_argument(
        "--database-url", default=os.environ.get("CONTEXTMESH_TEST_DATABASE_URL", default_url)
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("CONTEXTMESH_TEST_QDRANT_URL", "http://127.0.0.1:6333"),
    )
    parser.add_argument("--api-port", type=int, default=18000)
    parser.add_argument("--frontend-port", type=int, default=18001)
    parser.add_argument("--provider", choices=["openai", "openrouter"], default="openai")
    parsed = parser.parse_args()
    return Settings(
        parsed.database_url,
        parsed.qdrant_url,
        Path(tempfile.mkdtemp(prefix="contextmesh-smoke-blobs-")),
        parsed.api_port,
        parsed.frontend_port,
        f"e2e-{uuid4()}",
        str(uuid4()),
        parsed.provider,
    )


def require_qdrant(settings):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if qdrant_ready(settings.qdrant_url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Qdrant is not ready at {settings.qdrant_url}")


def qdrant_ready(url):
    try:
        with urllib.request.urlopen(f"{url}/readyz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def migrate(settings, provider_url):
    command = [
        "uv",
        "run",
        "--project",
        "backend",
        "--locked",
        "alembic",
        "-c",
        "backend/alembic.ini",
        "upgrade",
        "head",
    ]
    subprocess.run(command, cwd=ROOT, env=api_environment(settings, provider_url), check=True)


def configured_checks(page, settings, fixture, log_dir):
    index_fixture_document(settings)
    verify_replay(settings, fixture)
    verify_safe_failure(settings, fixture)
    verify_browser_sources(page, settings, log_dir)
    conversation_id = verify_browser_chat(page, settings, fixture)
    page.screenshot(path=str(log_dir / "desktop.png"), full_page=True)
    verify_ui_failure(page, settings, fixture)
    verify_mobile(page, settings, log_dir)
    return conversation_id


def browser_run(settings, fixture, provider_url, log_dir, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_default_timeout(15000)
    try:
        session_checks(page, settings, fixture, provider_url, log_dir)
    except Exception:
        page.screenshot(path=str(log_dir / "failure.png"), full_page=True)
        raise
    finally:
        page.close()


def session_checks(page, settings, fixture, provider_url, log_dir):
    with running_worker(settings, provider_url, log_dir):
        with running_api(settings, provider_url, log_dir), running_frontend(settings, log_dir):
            conversation_id = configured_checks(page, settings, fixture, log_dir)
        with running_api(settings, provider_url, log_dir):
            verify_history(settings, conversation_id)
    with running_api(settings, provider_url, log_dir, configured=False):
        verify_missing_configuration(settings, fixture)
        with running_frontend(settings, log_dir):
            verify_ui_missing_configuration(page, settings)


def main():
    settings = arguments()
    log_dir = Path(tempfile.mkdtemp(prefix="contextmesh-smoke-"))
    print(f"Runtime logs: {log_dir}", flush=True)
    require_qdrant(settings)
    with provider_server() as (fixture, provider_url), sync_playwright() as playwright:
        migrate(settings, provider_url)
        browser = playwright.chromium.launch(channel="chromium")
        try:
            browser_run(settings, fixture, provider_url, log_dir, browser)
        finally:
            browser.close()
    print(
        f"PASS: real browser/API/worker/PostgreSQL/Qdrant/{settings.model_provider} smoke "
        "(controlled provider fixture)"
    )
    print(
        "Verified upload->index->cited answer, replay, safe failure/retry, history/restart, "
        "no-key, inert HTML, mobile/keyboard."
    )


if __name__ == "__main__":
    main()
