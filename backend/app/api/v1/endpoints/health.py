"""Process health transport depends only on the application health service."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import health_service
from app.services.health_service import HealthService


def live() -> dict[str, str]:
    return {"status": "ok"}


def ready(health: Annotated[HealthService, Depends(health_service)]) -> dict[str, str]:
    health.require_ready()
    return {"status": "ready"}


def health_router() -> APIRouter:
    router = APIRouter(prefix="/health")
    router.add_api_route("/live", live, response_model=None)
    router.add_api_route("/ready", ready, response_model=None)
    return router
