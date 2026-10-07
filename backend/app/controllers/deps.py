"""HTTP dependency keys and immutable per-application bindings."""

from dataclasses import dataclass

from app.services.chat_service import ConversationService
from app.services.health_service import HealthService
from app.services.sources import SourceService
from app.utils.security import Identity


def conversation_service() -> ConversationService:
    raise RuntimeError("The conversation service dependency has not been configured.")


def principal() -> Identity:
    raise RuntimeError("The server identity dependency has not been configured.")


def health_service() -> HealthService:
    raise RuntimeError("The health service dependency has not been configured.")


def source_service() -> SourceService:
    raise RuntimeError("The source service dependency has not been configured.")


@dataclass(frozen=True)
class ChatDependencies:
    service: ConversationService
    identity: Identity

    def conversation_service(self) -> ConversationService:
        return self.service

    def principal(self) -> Identity:
        return self.identity


@dataclass(frozen=True)
class HealthDependencies:
    service: HealthService

    def health_service(self) -> HealthService:
        return self.service


@dataclass(frozen=True)
class SourceDependencies:
    service: SourceService

    def source_service(self) -> SourceService:
        return self.service
