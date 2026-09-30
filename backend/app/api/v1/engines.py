from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_platform, get_principal
from app.application.platform import Platform
from app.application.principal import Principal

router = APIRouter(prefix="/engines", tags=["engines"])


@router.get("")
def list_engines(
    principal: Principal = Depends(get_principal), platform: Platform = Depends(get_platform)
) -> list[dict[str, Any]]:
    return [db.catalog().to_dict() for db in platform.registry.databases()]
