"""Command-line entry point: `python -m chess_analytics <command>`.

A thin shell around services, used until the worker exists (Phase 5). It only wires
settings, logging, the database and the source together; all logic lives in services.

    uv run python -m chess_analytics sync <username> [--full-refresh]
    uv run python -m chess_analytics process [--limit N]

Exit codes: 0 success, 1 sync failed or player not found, 2 invalid arguments.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.config import Settings, get_settings
from chess_analytics.ingestion.base import PlayerNotFoundError
from chess_analytics.ingestion.chesscom.client import ChessComClient
from chess_analytics.logging_setup import configure_logging
from chess_analytics.services.process import process_pending
from chess_analytics.services.sync import sync_player
from chess_analytics.storage.db import create_db_engine, create_session_factory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m chess_analytics")
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser(
        "sync", help="fetch a Chess.com player's games, then process new ones into core"
    )
    sync.add_argument("username", help="Chess.com username (case-insensitive)")
    sync.add_argument(
        "--full-refresh",
        action="store_true",
        help="ignore stored ETags and download every archive again",
    )

    process = commands.add_parser(
        "process", help="process raw games that need it into the core tables"
    )
    process.add_argument("--limit", type=int, default=None, help="process at most N games")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    engine = create_db_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        if args.command == "sync":
            return _sync(args, settings, session_factory)
        return _process(args, session_factory)
    finally:
        engine.dispose()


def _sync(
    args: argparse.Namespace, settings: Settings, session_factory: sessionmaker[Session]
) -> int:
    try:
        with ChessComClient.from_settings(settings) as source:
            summary = sync_player(
                args.username,
                source=source,
                session_factory=session_factory,
                full_refresh=args.full_refresh,
            )
    except PlayerNotFoundError:
        print(f"Chess.com player not found: {args.username}", file=sys.stderr)
        return 1

    print(
        f"{summary.status}: run {summary.run_id} for {summary.username} | "
        f"archives {summary.archives_checked} ({summary.archives_not_modified} unchanged) | "
        f"games seen {summary.games_seen} | new raw versions {summary.new_raw_versions} | "
        f"processed {summary.games_processed} | rejected {summary.games_rejected}"
    )
    if summary.error:
        print(f"error: {summary.error}", file=sys.stderr)
    return 0 if summary.status == "succeeded" else 1


def _process(args: argparse.Namespace, session_factory: sessionmaker[Session]) -> int:
    summary = process_pending(session_factory, limit=args.limit)
    print(
        f"pending {summary.pending} | processed {summary.processed} | "
        f"rejected {summary.rejected} (see core.processing_failures)"
    )
    return 0
