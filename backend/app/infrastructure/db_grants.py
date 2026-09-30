"""Table privileges for the platform's database logins (Cloud SQL IAM users on GKE).

The migration job owns the schema. Each backend role logs in as its own IAM user, which starts
without table privileges; this grants them data access (not DDL) to current and future tables:

    DB_APP_ROLES="byoc-api@proj.iam,byoc-cluster-manager@proj.iam" python -m app.infrastructure.db_grants
"""

from __future__ import annotations

import os

from sqlalchemy import Engine, text

from app.config.settings import get_settings
from app.infrastructure.db import create_db_engine
from app.infrastructure.logging import configure_logging, get_logger

log = get_logger(__name__)


def grant_app_roles(engine: Engine, roles: list[str]) -> list[str]:
    if engine.dialect.name != "postgresql":
        return []
    quote = engine.dialect.identifier_preparer.quote_identifier
    granted = []
    with engine.begin() as conn:
        for role in roles:
            name = quote(role)
            for statement in (
                f"GRANT USAGE ON SCHEMA public TO {name}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {name}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {name}",
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {name}",
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {name}",
            ):
                conn.execute(text(statement))
            granted.append(role)
    return granted


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format, "migrate")
    roles = [r.strip() for r in os.environ.get("DB_APP_ROLES", "").split(",") if r.strip()]
    engine = create_db_engine(settings.database_url)
    try:
        log.info("database_roles_granted", roles=grant_app_roles(engine, roles))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
