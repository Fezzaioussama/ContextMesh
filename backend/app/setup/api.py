"""FastAPI composition root; test substitutions are explicit factory arguments."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.controllers import deps
from app.controllers.deps import ChatDependencies, HealthDependencies, SourceDependencies
from app.controllers.errors import install_error_handlers
from app.controllers.health import health_router
from app.controllers.router import api_router
from app.setup.runtime import ApiRuntime, build_runtime, lifespan_for
from app.setup.services import ExternalServices
from app.utils.config import Settings
from app.utils.logging import show_flows
from app.utils.security import Identity


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
    show_flows()
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
