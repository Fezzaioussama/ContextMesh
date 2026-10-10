"""HTTP plumbing shared by every route: the server identity, health checks, request
ids, and safe error responses that never echo input or SDK exceptions."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.services.models import Identity
from app.utils.errors import ContextMeshError
from app.utils.logging import http_logger as logger


def server_identity(request: Request) -> Identity:
    """Bound when the app is created; no request header can change it."""
    return request.app.state.identity


Principal = Annotated[Identity, Depends(server_identity)]

health_router = APIRouter(prefix="/health")


@health_router.get("/live", response_model=None)
def live() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/ready", response_model=None)
def ready(request: Request) -> dict[str, str]:
    if not request.app.state.database_ready():
        raise ContextMeshError("database_unavailable", "The database schema is unavailable.")
    return {"status": "ready"}


def error_response(
    request: Request, code: str, message: str, status: int, retryable: bool = False
) -> JSONResponse:
    request_id = request.state.request_id
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "retryable": retryable,
            }
        },
        headers={"X-Request-ID": request_id},
    )


async def known_error(request: Request, error: ContextMeshError) -> JSONResponse:
    return error_response(request, error.code, error.message, error.status, error.retryable)


async def validation_error(request: Request, error: Exception) -> JSONResponse:
    return error_response(request, "invalid_input", "The request contains invalid input.", 422)


async def database_error(request: Request, error: Exception) -> JSONResponse:
    message = "The database is unavailable."
    return error_response(request, "database_unavailable", message, 503, retryable=True)


async def http_error(request: Request, error: HTTPException) -> JSONResponse:
    message = "The requested operation is unavailable."
    return error_response(request, "http_error", message, error.status_code)


async def request_context(request: Request, call_next):
    """Give every request a server-assigned id; unexpected failures become a safe 500."""
    request.state.request_id = str(uuid4())
    try:
        response = await call_next(request)
    except Exception:
        logger.error("Unexpected request failure; request_id=%s", request.state.request_id)
        message = "The request could not be completed."
        response = error_response(request, "internal_error", message, 500)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ContextMeshError, known_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(SQLAlchemyError, database_error)
    app.add_exception_handler(HTTPException, http_error)
    app.middleware("http")(request_context)
