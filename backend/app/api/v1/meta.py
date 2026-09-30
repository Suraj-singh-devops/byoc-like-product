from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_session
from app.api.v1.schemas import MetaResponse
from app.application.platform import Platform
from app.application.seed import DEMO_ACCOUNTS
from app.models import User

router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
def meta(platform: Platform = Depends(get_platform), session: Session = Depends(get_session)) -> MetaResponse:
    settings = platform.settings
    demo = []
    if settings.mock_mode and settings.seed_demo_data:
        # Only demo accounts that still exist (a local database may have removed some).
        emails = [email for _, email, _, _ in DEMO_ACCOUNTS]
        existing = set(session.scalars(select(User.email).where(User.email.in_(emails))))
        demo = [
            {"email": email, "role": role.value, "organization": org}
            for org, email, _, role in DEMO_ACCOUNTS
            if email in existing
        ]
    return MetaResponse(
        name=settings.app_name,
        version=settings.version,
        mock_mode=settings.mock_mode,
        signup_enabled=settings.allow_signup,
        demo_accounts=demo,
    )
