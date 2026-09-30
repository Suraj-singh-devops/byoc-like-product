"""Demo organizations and users for local development (SEED_DEMO_DATA=true).

Two organizations make tenant isolation easy to try: Acme has one user per role, Globex
has a single owner who must never see Acme's resources.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.domain.enums import Role
from app.infrastructure.db import SessionFactory, session_scope
from app.infrastructure.logging import get_logger
from app.infrastructure.security import MIN_PASSWORD_LENGTH, hash_password
from app.models import Organization, OrganizationMember, User

log = get_logger(__name__)

DEMO_ACCOUNTS: tuple[tuple[str, str, str, Role], ...] = (
    ("acme", "owner@acme.example", "Olivia Owner", Role.OWNER),
    ("acme", "admin@acme.example", "Adam Admin", Role.ADMIN),
    ("acme", "operator@acme.example", "Oscar Operator", Role.OPERATOR),
    ("acme", "viewer@acme.example", "Victor Viewer", Role.VIEWER),
    ("globex", "owner@globex.example", "Grace Globex", Role.OWNER),
)
DEMO_ORGS = {"acme": "Acme Corp", "globex": "Globex"}
# Demo user of databases seeded before the Developer role became Operator.
LEGACY_DEMO_USER = "developer@acme.example"


def seed_demo_data(session_factory: SessionFactory, settings: Settings) -> bool:
    if not settings.seed_demo_data:
        return False
    if len(settings.demo_password) < MIN_PASSWORD_LENGTH:
        log.warning("demo_seed_skipped", reason=f"DEMO_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters")
        return False
    with session_scope(session_factory) as s:
        if (s.scalar(select(func.count()).select_from(User)) or 0) > 0:
            _rename_legacy_demo_user(s)
            return False
        orgs = {slug: Organization(name=name, slug=slug) for slug, name in DEMO_ORGS.items()}
        s.add_all(orgs.values())
        s.flush()
        password_hash = hash_password(settings.demo_password)
        for slug, email, name, role in DEMO_ACCOUNTS:
            user = User(email=email, name=name, password_hash=password_hash)
            s.add(user)
            s.flush()
            s.add(OrganizationMember(organization_id=orgs[slug].id, user_id=user.id, role=role.value))
    log.info("demo_data_seeded", accounts=[a[1] for a in DEMO_ACCOUNTS])
    return True


def _rename_legacy_demo_user(session: Session) -> None:
    """Keep the login page's demo list valid for local databases seeded by the v1 prototype."""
    legacy = session.scalar(select(User).where(User.email == LEGACY_DEMO_USER))
    if legacy is None or session.scalar(select(User.id).where(User.email == "operator@acme.example")) is not None:
        return
    legacy.email = "operator@acme.example"
    legacy.name = "Oscar Operator"
    log.info("demo_user_renamed", old=LEGACY_DEMO_USER, new=legacy.email)
