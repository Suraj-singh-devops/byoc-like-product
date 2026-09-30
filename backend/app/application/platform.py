"""Long-lived dependencies shared by the API and the workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config.settings import Settings
from app.infrastructure.db import SessionFactory
from app.infrastructure.queue import OperationQueue
from app.infrastructure.rate_limit import RateLimiter
from app.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from app.application.tasks import CloudTasks


@dataclass
class Platform:
    settings: Settings
    session_factory: SessionFactory
    registry: ProviderRegistry
    queue: OperationQueue
    rate_limiter: RateLimiter
    # Cloud work for the terraform-runner and the monitoring-worker (app/application/tasks.py).
    tasks: CloudTasks
