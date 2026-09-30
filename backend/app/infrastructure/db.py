from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.logging import get_logger

log = get_logger(__name__)

SessionFactory = sessionmaker[Session]


def create_db_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: SessionFactory) -> Iterator[Session]:
    """A session that commits on success and rolls back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def wait_for_schema(engine: Engine, table: str = "operations", timeout: float = 120) -> None:
    """Block until migrations have created ``table`` (the worker starts alongside the API)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if inspect(engine).has_table(table):
                return
            reason = "schema not migrated yet"
        except Exception as exc:  # database not reachable yet
            reason = str(exc).splitlines()[0]
        if time.monotonic() > deadline:
            raise RuntimeError(f"Database not ready after {timeout}s: {reason}")
        log.info("waiting_for_database", reason=reason)
        time.sleep(2)
