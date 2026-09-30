"""Execution context of one operation run: step timeline, progress, cancellation."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.application.platform import Platform
from app.domain.errors import OperationCancelled, PlatformError
from app.domain.states import OperationStatus, assert_operation_transition
from app.infrastructure.db import session_scope
from app.infrastructure.logging import get_logger
from app.models import Operation

log = get_logger(__name__)

MAX_LOG_ENTRIES = 200
FLUSH_INTERVAL_SECONDS = 1.0
CANCEL_CHECK_INTERVAL_SECONDS = 2.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class StepDef:
    key: str
    name: str
    status: OperationStatus


class OperationContext:
    """Implements the ProgressReporter protocol the cloud providers use."""

    def __init__(self, platform: Platform, operation_id: uuid.UUID) -> None:
        self.platform = platform
        self.operation_id = operation_id
        with session_scope(platform.session_factory) as s:
            op = s.get(Operation, operation_id)
            if op is None:
                raise LookupError(f"operation {operation_id} not found")
            self.operation_type = op.operation_type
            self.cluster_id = op.cluster_id
            self.organization_id = op.organization_id
            self.created_by_id = op.created_by_id
            self.params: dict[str, Any] = dict((op.metadata_ or {}).get("params", {}))
            self.status = OperationStatus(op.status)
            self.attempt = op.attempt
        self.result: dict[str, Any] = {}
        self._defs: dict[str, StepDef] = {}
        self._steps: list[dict[str, Any]] = []
        self._current: str | None = None
        self._pending_log: list[dict[str, Any]] = []
        self._last_flush = 0.0
        self._last_cancel_check = 0.0

    # ------------------------------------------------------------------ steps

    def define_steps(self, steps: list[StepDef]) -> None:
        self._defs = {s.key: s for s in steps}
        self._steps = [
            {
                "key": s.key,
                "name": s.name,
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "message": None,
            }
            for s in steps
        ]
        note = f"Attempt {self.attempt} started (previous attempt did not finish)" if self.attempt > 1 else None
        self._flush(force=True, log_entry=note)

    def _entry(self, key: str) -> dict[str, Any]:
        return next(s for s in self._steps if s["key"] == key)

    @contextmanager
    def step(self, key: str) -> Iterator[None]:
        self.check_cancelled(force=True)
        definition = self._defs[key]
        if definition.status != self.status:
            assert_operation_transition(self.status, definition.status)
            self.status = definition.status
        entry = self._entry(key)
        entry.update(status="running", started_at=_now_iso(), message=None)
        self._current = key
        self._flush(force=True, log_entry=f"{definition.name}: started")
        try:
            yield
        except OperationCancelled:
            entry.update(status="cancelled", completed_at=_now_iso())
            self._flush(force=True, log_entry=f"{definition.name}: cancelled")
            raise
        except PlatformError as exc:
            entry.update(status="failed", completed_at=_now_iso(), message=str(exc)[:1000])
            self._flush(force=True, log_entry=f"{definition.name}: failed - {exc.message}")
            raise
        except Exception:
            entry.update(status="failed", completed_at=_now_iso(), message="Internal error")
            self._flush(force=True, log_entry=f"{definition.name}: failed - internal error")
            raise
        else:
            entry.update(status="completed", completed_at=_now_iso())
            self._flush(force=True, log_entry=f"{definition.name}: completed")
        finally:
            self._current = None

    # ------------------------------------------------------- ProgressReporter

    def message(self, text: str) -> None:
        if self._current is not None:
            self._entry(self._current)["message"] = text
        self._pending_log.append({"at": _now_iso(), "step": self._current, "message": text})
        self._flush()

    def heartbeat(self) -> None:
        self._flush()

    def check_cancelled(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_cancel_check < CANCEL_CHECK_INTERVAL_SECONDS:
            return
        self._last_cancel_check = now
        with session_scope(self.platform.session_factory) as s:
            requested = s.scalar(select(Operation.cancel_requested).where(Operation.id == self.operation_id))
        if requested:
            raise OperationCancelled()

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self.check_cancelled()
            self.heartbeat()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.5))

    # ------------------------------------------------------------------ flush

    def flush(self) -> None:
        self._flush(force=True)

    def _flush(self, force: bool = False, log_entry: str | None = None) -> None:
        if log_entry:
            self._pending_log.append({"at": _now_iso(), "step": self._current, "message": log_entry})
        now = time.monotonic()
        if not force and now - self._last_flush < FLUSH_INTERVAL_SECONDS:
            return
        self._last_flush = now
        done = sum(1 for s in self._steps if s["status"] in ("completed", "skipped"))
        progress = int(100 * done / len(self._steps)) if self._steps else 0
        with session_scope(self.platform.session_factory) as s:
            op = s.get(Operation, self.operation_id)
            if op is None:
                return
            metadata = dict(op.metadata_ or {})
            metadata["steps"] = [dict(step) for step in self._steps]
            metadata["log"] = (list(metadata.get("log") or []) + self._pending_log)[-MAX_LOG_ENTRIES:]
            op.metadata_ = metadata
            op.status = self.status.value
            op.current_step = self._current
            op.progress = progress
            op.heartbeat_at = datetime.now(UTC)
        self._pending_log = []
