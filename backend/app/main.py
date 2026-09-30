"""FastAPI application factory.

uvicorn app.main:create_app --factory
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api import system
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.v1.router import PREFIX, build_router
from app.application.platform import Platform
from app.application.seed import seed_demo_data
from app.application.task_handlers import inline_tasks
from app.application.tasks import CloudTasks, RedisTaskWaker
from app.config.settings import Settings, get_settings
from app.infrastructure.db import create_db_engine, create_session_factory
from app.infrastructure.logging import configure_logging, get_logger
from app.infrastructure.queue import RedisOperationQueue, SafeEnqueuer
from app.infrastructure.rate_limit import RedisRateLimiter
from app.providers.registry import build_registry

log = get_logger(__name__)


def build_platform(settings: Settings) -> Platform:
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    client = redis.Redis.from_url(settings.redis_dsn, socket_timeout=5, socket_connect_timeout=5)
    platform = Platform(
        settings=settings,
        session_factory=session_factory,
        registry=build_registry(settings, session_factory),
        queue=SafeEnqueuer(RedisOperationQueue(client)),
        rate_limiter=RedisRateLimiter(client),
        # The API has no cloud access (docs/adr/0004): lookups and validations go to the workers.
        tasks=CloudTasks(session_factory, RedisTaskWaker(client)),
    )
    if settings.service_role == "all":
        platform.tasks = inline_tasks(platform)
    return platform


def create_app(settings: Settings | None = None, platform: Platform | None = None) -> FastAPI:
    settings = settings or (platform.settings if platform else get_settings())
    if platform is None:
        configure_logging(settings.log_level, settings.log_format, settings.service_name)
        platform = build_platform(settings)
    if settings.uses_insecure_secret:
        log.warning("insecure_secret_key", hint="SECRET_KEY uses the development default; never deploy this.")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await run_in_threadpool(seed_demo_data, platform.session_factory, settings)
        log.info("api_started", mock_mode=settings.mock_mode, version=settings.version)
        yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Managed database experience on customer-owned cloud infrastructure.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url=f"{PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.platform = platform
    register_exception_handlers(app)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(build_router(settings))
    app.include_router(system.router)
    return app
