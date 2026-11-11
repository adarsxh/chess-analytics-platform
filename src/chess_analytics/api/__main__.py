"""Run the API server:  `python -m chess_analytics.api`  (or `uv run python -m ...`)."""

import uvicorn

from chess_analytics.config import get_settings
from chess_analytics.logging_setup import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(
        "chess_analytics.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
        # Don't let uvicorn install its own log handlers: its loggers then propagate to
        # the root logger, which configure_logging() set up, so its lines are JSON too.
        log_config=None,
    )


if __name__ == "__main__":
    main()
