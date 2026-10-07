"""FastAPI composition root; test substitutions are explicit factory arguments."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import deps
from app.api.deps import ChatDependencies, HealthDependencies, SourceDependencies
from app.api.errors import install_error_handlers
from app.api.v1.endpoints.health import health_router
from app.api.v1.router import api_router
from app.bootstrap.runtime import ApiRuntime, build_runtime, lifespan_for
from app.bootstrap.services import ExternalServices
from app.core.config import Settings
from app.core.security import Identity


def create_app(
    settings: Settings | None = None,
    services: ExternalServices | None = None,
    identity: Identity | None = None,
) -> FastAPI:
    config = settings
    if config is None:
        config = Settings()
    principal = identity
    if principal is None:
        principal = Identity(config.dev_subject, config.dev_workspace_id)
    runtime = build_runtime(config, services)
    try:
        return compose_app(config, principal, runtime)
    except BaseException:
        runtime.close()
        raise


def compose_app(config: Settings, principal: Identity, runtime: ApiRuntime) -> FastAPI:
    app = FastAPI(
        title="ContextMesh",
        version="0.2.0",
        lifespan=lifespan_for(runtime, principal),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    install_error_handlers(app)
    chat = ChatDependencies(runtime.conversations, principal)
    knowledge = SourceDependencies(runtime.sources)
    health = HealthDependencies(runtime.health)
    app.dependency_overrides.update(
        {
            deps.conversation_service: chat.conversation_service,
            deps.principal: chat.principal,
            deps.source_service: knowledge.source_service,
            deps.health_service: health.health_service,
        }
    )
    app.include_router(health_router())
    app.include_router(api_router())
    return app
