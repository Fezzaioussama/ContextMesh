"""The API process: `uvicorn app.main:create_app --factory`."""

from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.controllers import chat, sources
from app.controllers.http import health_router, install_error_handlers
from app.services.models import Identity
from app.utils.config import Settings
from app.utils.logging import show_flows
from app.wiring import Api, ExternalServices, build_api


def create_app(
    settings: Settings | None = None,
    services: ExternalServices | None = None,
    identity: Identity | None = None,
) -> FastAPI:
    """Tests pass their own settings, external services (which they keep owning), and
    server identity."""
    config = settings or Settings()
    identity = identity or Identity(config.dev_subject, config.dev_workspace_id)
    with ExitStack() as stack:
        api = build_api(config, services, stack)
        resources = stack.pop_all()
    show_flows()
    try:
        return compose_app(config, identity, api, resources)
    except BaseException:
        resources.close()
        raise


def compose_app(config: Settings, identity: Identity, api: Api, resources: ExitStack) -> FastAPI:
    """Routes, middleware, and the built frontend when configured.

    `resources` close when the app shuts down.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await run_in_threadpool(_provision, api, identity)
            yield
        finally:
            await run_in_threadpool(resources.close)

    app = FastAPI(title="ContextMesh", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    install_error_handlers(app)
    app.state.identity = identity
    app.state.conversations = api.conversations
    app.state.sources = api.sources
    app.state.database_ready = api.database_ready
    app.include_router(health_router)
    app.include_router(chat.router)
    app.include_router(sources.router)
    if config.frontend_dir is not None:
        # Mounted last, so API routes match first; one origin needs no CORS in production.
        app.mount("/", StaticFiles(directory=config.frontend_dir, html=True), name="frontend")
    return app


def _provision(api: Api, identity: Identity) -> None:
    try:
        api.provision(identity)
    except SQLAlchemyError:
        pass  # Liveness stays available; readiness and operations report the database.
