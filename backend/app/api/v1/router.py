"""Assemble the implemented API endpoints without changing their public paths."""

from fastapi import APIRouter

from app.api.v1.endpoints.chat import assistant_router
from app.api.v1.endpoints.sources import knowledge_router


def api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(assistant_router())
    router.include_router(knowledge_router())
    return router
