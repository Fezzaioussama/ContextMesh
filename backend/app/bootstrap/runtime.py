"""Own each API instance's resources, including cleanup after startup failures."""

from contextlib import AbstractAsyncContextManager, ExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.bootstrap.conversations import conversation_service, create_model
from app.bootstrap.schema import REQUIRED_TABLES, SCHEMA_REVISION
from app.core.config import Settings
from app.core.security import Identity
from app.db.health import DatabaseHealth
from app.db.repositories.user_repository import DevelopmentIdentityRepository
from app.db.session import create_database_engine
from app.services.chat_service import ConversationService
from app.services.health_service import HealthService
from app.services.ports.models import ChatModel


@dataclass(frozen=True)
class ApiRuntime:
    conversations: ConversationService
    health: HealthService
    identity_repository: DevelopmentIdentityRepository
    resources: ExitStack

    def provision(self, identity: Identity) -> None:
        try:
            self.identity_repository.provision(identity)
        except SQLAlchemyError:
            pass  # Liveness is available; readiness and operations report database failure.

    def close(self) -> None:
        self.resources.close()


def build_runtime(config: Settings, model: ChatModel | None) -> ApiRuntime:
    with ExitStack() as resources:
        engine = create_database_engine(config.database_url)
        resources.callback(engine.dispose)
        if model is None:
            model = create_model(config)
            resources.callback(model.close)
        return ApiRuntime(
            conversation_service(engine, config, model),
            HealthService(DatabaseHealth(engine, REQUIRED_TABLES, SCHEMA_REVISION)),
            DevelopmentIdentityRepository(engine),
            resources.pop_all(),
        )


def lifespan_for(
    runtime: ApiRuntime, identity: Identity
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await run_in_threadpool(runtime.provision, identity)
            yield
        finally:
            await run_in_threadpool(runtime.close)

    return lifespan
