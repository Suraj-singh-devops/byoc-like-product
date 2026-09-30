"""Consistent error envelope: {"error": {code, message, reason?, suggested_action?, details?, request_id}}.

Stack traces never reach clients; unexpected errors are logged with the request ID.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import PlatformError
from app.infrastructure.logging import get_logger
from app.infrastructure.metrics import API_ERRORS

log = get_logger(__name__)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_response(request: Request, status_code: int, body: dict[str, Any]) -> JSONResponse:
    body = {**body, "request_id": _request_id(request)}
    route = getattr(request.scope.get("route"), "path", "unmatched")
    API_ERRORS.labels(route, body.get("code", "UNKNOWN")).inc()
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(status_code=status_code, content={"error": body}, headers=headers)


async def platform_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PlatformError)
    if exc.status_code >= 500:
        log.error("platform_error", code=exc.code, error=str(exc))
    return error_response(request, exc.status_code, exc.to_dict())


def _field_name(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or "request"


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields: dict[str, str] = {}
    for error in exc.errors():
        fields.setdefault(_field_name(tuple(error.get("loc", ()))), str(error.get("msg", "Invalid value")))
    return error_response(
        request,
        422,
        {
            "code": "VALIDATION_FAILED",
            "message": "The request is invalid.",
            "suggested_action": "Correct the highlighted fields and try again.",
            "details": {"fields": fields},
        },
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "AUTHENTICATION_FAILED", 403: "PERMISSION_DENIED"}
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    if exc.status_code == 404 and message == "Not Found":
        message = "The requested resource was not found."
    return error_response(
        request, exc.status_code, {"code": codes.get(exc.status_code, "HTTP_ERROR"), "message": message}
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PlatformError, platform_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
