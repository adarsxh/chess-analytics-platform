"""FastAPI application factory.

`create_app()` builds a fresh app instance. Using a factory (instead of a module-level
`app = FastAPI()`) means tests can build an app with their own settings, and nothing
happens just by importing this module.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from chess_analytics import __version__
from chess_analytics.api.routes import health
from chess_analytics.config import Settings, get_settings
from chess_analytics.logging_setup import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runs once at startup (before `yield`) and once at shutdown (after).
    # Later steps open/close the database engine here.
    settings: Settings = app.state.settings
    log.info("api_started", env=settings.env, version=__version__)
    yield
    log.info("api_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(title="Chess Analytics API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    return app
