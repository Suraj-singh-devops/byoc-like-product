"""Settings and database setup the GKE deployment relies on (implementation plan P3)."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.config.settings import Settings
from app.infrastructure.db_grants import grant_app_roles


def test_redis_password_comes_separately() -> None:
    assert Settings(redis_url="redis://redis:6379/0").redis_dsn == "redis://redis:6379/0"
    dsn = Settings(redis_url="redis://redis:6379/0", redis_password="p@ss/word").redis_dsn
    assert dsn == "redis://:p%40ss%2Fword@redis:6379/0"


def test_grants_are_skipped_outside_postgresql() -> None:
    assert grant_app_roles(create_engine("sqlite://"), ["byoc-api@proj.iam"]) == []


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"), reason="needs PostgreSQL")
def test_iam_style_roles_get_data_access_to_current_and_future_tables() -> None:
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    role = f"byoc-api-{uuid.uuid4().hex[:6]}@proj.iam"
    table = f"grant_probe_{uuid.uuid4().hex[:6]}"
    try:
        with engine.begin() as c:
            c.execute(text(f'CREATE ROLE "{role}"'))
            c.execute(text(f"CREATE TABLE {table} (id int)"))
        assert grant_app_roles(engine, [role]) == [role]
        with engine.begin() as c:
            c.execute(text(f"CREATE TABLE {table}_later (id int)"))
            for name in (table, f"{table}_later"):
                allowed = c.execute(text(f"SELECT has_table_privilege('{role}', '{name}', 'INSERT')")).scalar()
                assert allowed, name
            # Data access only: the migration job alone changes the schema.
            assert not c.execute(text(f"SELECT has_schema_privilege('{role}', 'public', 'CREATE')")).scalar()
    finally:
        with engine.begin() as c:
            c.execute(text(f"DROP TABLE IF EXISTS {table}, {table}_later"))
            c.execute(text(f'DROP OWNED BY "{role}"'))
            c.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        engine.dispose()
