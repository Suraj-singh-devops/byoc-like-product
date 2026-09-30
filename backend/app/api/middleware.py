from __future__ import annotations

import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infrastructure.logging import bind_context, get_logger
from app.infrastructure.metrics import API_LATENCY, API_REQUESTS

log = get_logger("app.api")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)


class RequestContextMiddleware:
    """Request ID, access log, Prometheus metrics, security headers and a last-resort
    500 handler that never exposes internals."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = {"code": 500, "sent": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                status["sent"] = True
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for name, value in SECURITY_HEADERS:
                    if name not in headers:
                        headers[name] = value
            await send(message)

        with bind_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception:
                log.exception("unhandled_exception", path=scope.get("path"))
                status["code"] = 500
                if not status["sent"]:
                    response = JSONResponse(
                        status_code=500,
                        content={
                            "error": {
                                "code": "INTERNAL_ERROR",
                                "message": "An internal error occurred.",
                                "suggested_action": "Try again. If it persists, contact support with the request ID.",
                                "request_id": request_id,
                            }
                        },
                    )
                    await response(scope, receive, send_wrapper)
            finally:
                duration = time.perf_counter() - started
                route = getattr(scope.get("route"), "path", "unmatched")
                method = scope.get("method", "")
                API_REQUESTS.labels(method, route, str(status["code"])).inc()
                API_LATENCY.labels(method, route).observe(duration)
                path = scope.get("path", "")
                if path not in _QUIET_PATHS:
                    log.info(
                        "http_request",
                        method=method,
                        path=path,
                        route=route,
                        status=status["code"],
                        duration_ms=round(duration * 1000, 1),
                        user_id=(scope.get("state") or {}).get("user_id"),
                    )
