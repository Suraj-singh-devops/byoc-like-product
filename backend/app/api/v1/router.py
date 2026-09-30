"""The /api/v1 router: every versioned endpoint of the control plane."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agent,
    audit_logs,
    auth,
    cloud_accounts,
    clusters,
    engines,
    environments,
    events,
    meta,
    mock,
    networks,
    operations,
    organizations,
)
from app.config.settings import Settings

PREFIX = "/api/v1"


def build_router(settings: Settings) -> APIRouter:
    api = APIRouter(prefix=PREFIX)
    for module in (
        auth,
        organizations,
        cloud_accounts,
        environments,
        networks,
        clusters,
        events,
        operations,
        audit_logs,
        engines,
        agent,
        meta,
    ):
        api.include_router(module.router)
    api.include_router(cloud_accounts.providers_router)
    if settings.mock_mode:
        api.include_router(mock.router)
    return api
