"""Structured logging setup shared by every entrypoint (API, worker, CLI).

Usage:
    configure_logging(level="INFO", fmt="json")      # once, at process start
    log = structlog.get_logger(__name__)
    log.info("archive_fetched", month="2026-10", games=118)

Both structlog loggers *and* stdlib loggers from third-party libraries (uvicorn, httpx,
SQLAlchemy) go through the same formatter, so every line has the same shape.
"""

import logging
import sys
from typing import Literal

import structlog
from structlog.typing import Processor

# Processors applied to every event, whether it came from structlog or stdlib logging.
_SHARED_PROCESSORS: list[Processor] = [
    structlog.contextvars.merge_contextvars,  # pulls in bind_contextvars(job_id=...) values
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
]


def configure_logging(level: str = "INFO", fmt: Literal["console", "json"] = "json") -> None:
    """Configure structlog and route stdlib logging through the same renderer."""
    renderer: list[Processor] = (
        [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
        if fmt == "json"
        else [structlog.dev.ConsoleRenderer()]
    )

    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()  # idempotent: calling twice must not duplicate output
    root.addHandler(handler)
    root.setLevel(level)
