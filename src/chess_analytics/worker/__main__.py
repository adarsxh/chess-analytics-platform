"""Worker process entry point: `python -m chess_analytics.worker`.

Stopping: the first SIGINT (Ctrl+C) or SIGTERM (`docker stop`) lets the current job
finish, then exits. A second signal exits immediately; any job it interrupts is
recovered by another worker once its lease expires.
"""

import os
import signal
import socket
import threading
from datetime import timedelta
from types import FrameType

import structlog

from chess_analytics.config import get_settings
from chess_analytics.logging_setup import configure_logging
from chess_analytics.storage.db import create_db_engine, create_session_factory
from chess_analytics.worker.handlers import build_handlers, stockfish_holder
from chess_analytics.worker.runner import Worker

log = structlog.get_logger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    stop = threading.Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        if stop.is_set():
            log.warning("worker_forced_exit")
            raise SystemExit(1)
        log.info("worker_stopping", signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    engines = stockfish_holder(settings)  # Stockfish starts on the first job that needs it
    worker = Worker(
        session_factory,
        build_handlers(settings, session_factory, engines),
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
        poll_interval=settings.worker_poll_interval_seconds,
        lease=timedelta(seconds=settings.worker_lease_seconds),
    )
    try:
        worker.run(stop)
    finally:
        engines.close()
        engine.dispose()


main()
