"""Safe errors and server-assigned request IDs, without input/SDK exception leakage."""

from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.core.exceptions import ContextMeshError
from app.core.logging import http_logger as logger

ERROR_STATUS = {
    "not_found": 404,
    "turn_in_progress": 409,
    "idempotency_conflict": 409,
    "invalid_input": 422,
    "provider_unavailable": 503,
    "provider_not_configured": 503,
    "database_unavailable": 503,
}


def error_response(request, code, message, status, retryable=False):
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


async def chat_error(request, error: ContextMeshError):
    status = ERROR_STATUS.get(error.code, 500)
    return error_response(request, error.code, error.message, status, error.retryable)


async def validation_error(request, error):
    return error_response(request, "invalid_input", "The request contains invalid input.", 422)


async def database_error(request, error):
    return error_response(
        request, "database_unavailable", "The database is unavailable.", 503, True
    )


async def http_error(request, error):
    return error_response(
        request, "http_error", "The requested operation is unavailable.", error.status_code
    )


async def request_context(request, call_next):
    request.state.request_id = str(uuid4())
    try:
        response = await call_next(request)
    except Exception:
        logger.error("Unexpected request failure; request_id=%s", request.state.request_id)
        response = error_response(
            request, "internal_error", "The request could not be completed.", 500
        )
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def install_error_handlers(app):
    app.add_exception_handler(ContextMeshError, chat_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(SQLAlchemyError, database_error)
    app.add_exception_handler(HTTPException, http_error)
    app.middleware("http")(request_context)
