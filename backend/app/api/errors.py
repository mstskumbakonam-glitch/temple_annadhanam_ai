"""Exception handlers producing consistent error bodies.

SQLAlchemy errors are logged in full server-side but never returned to clients,
so no SQL, table names or stack traces leak.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.services.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Starlette renamed 422 to HTTP_422_UNPROCESSABLE_CONTENT; fall back on older versions.
HTTP_422 = getattr(
    status, "HTTP_422_UNPROCESSABLE_CONTENT", None
) or status.HTTP_422_UNPROCESSABLE_ENTITY

STATUS_BY_ERROR: dict[type[ServiceError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    ValidationError: HTTP_422,
}


def _body(detail: str, code: str) -> dict:
    return {"detail": detail, "code": code}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def handle_service_error(_: Request, exc: ServiceError) -> JSONResponse:
        http_status = STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=http_status, content=_body(exc.detail, exc.code))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """422 with field-level detail, in the same shape as every other error."""
        problems = [
            f"{'.'.join(str(part) for part in error['loc'][1:]) or 'body'}: {error['msg']}"
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=HTTP_422,
            content={
                "detail": "Request validation failed: " + "; ".join(problems),
                "code": "invalid_request",
                "errors": problems,
            },
        )

    @app.exception_handler(OperationalError)
    async def handle_operational_error(_: Request, exc: OperationalError) -> JSONResponse:
        """The database is unreachable: 503, with the driver message kept internal."""
        logger.error("Database unavailable: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_body(
                "The database is currently unavailable. Please try again shortly.",
                "database_unavailable",
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("Unhandled database error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_body("An internal database error occurred.", "internal_error"),
        )
