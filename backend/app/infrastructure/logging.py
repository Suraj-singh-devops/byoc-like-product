"""Structured (JSON) logging with request/operation context and secret redaction.

Usage::

    log = get_logger(__name__)
    with bind_context(operation_id=op.id, cluster_id=cluster.id):
        log.info("terraform_apply_completed", duration_seconds=41.2)
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

_context: contextvars.ContextVar[Mapping[str, Any]] = contextvars.ContextVar(
    "log_context", default=MappingProxyType({})
)

_SENSITIVE_MARKERS = (
    "password",
    "passphrase",
    "secret",
    "token",
    "private_key",
    "service_account_key",
    "access_key",
    "encryption_key",
    "signing_key",
    "api_key",
    "credential",
    "authorization",
    "cookie",
)
REDACTED = "[REDACTED]"


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(marker in k for marker in _SENSITIVE_MARKERS)


def redact(value: Any, key: str = "") -> Any:
    if key and _is_sensitive(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


@contextmanager
def bind_context(**fields: Any) -> Iterator[None]:
    current = _context.get()
    token = _context.set({**current, **{k: str(v) for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_context.get())
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(redact(fields))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S.%f")[:-3]
        extras = {**_context.get(), **redact(getattr(record, "fields", None) or {})}
        kv = " ".join(f"{k}={v}" for k, v in extras.items())
        line = f"{ts} {record.levelname:<7} [{self.service}] {record.getMessage()}"
        if kv:
            line += f"  {kv}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class StructLogger:
    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, event: str, exc_info: bool = False, **fields: Any) -> None:
        if self._logger.isEnabledFor(level):
            self._logger.log(level, event, exc_info=exc_info, extra={"fields": fields}, stacklevel=3)

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, exc_info=True, **fields)


def get_logger(name: str) -> StructLogger:
    return StructLogger(name)


def configure_logging(level: str = "INFO", fmt: str = "json", service: str = "api") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service) if fmt == "json" else ConsoleFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Route uvicorn's loggers through ours; request logs come from our own middleware.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for noisy in ("urllib3", "google.auth", "httpx", "httpcore", "alembic.runtime.migration"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
