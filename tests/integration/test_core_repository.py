"""Core repository against the real schema: players by uuid, game upserts, failures,
and the "needs processing" query."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from chess_analytics.domain.game import Color, GameResult, Outcome, ParsedPlayer
from chess_analytics.storage.models.core import (
    Game,
    GameMove,
    GamePlayer,
    Player,
    ProcessingFailure,
)
from chess_analytics.storage.models.raw import RawGame
from chess_analytics.storage.repositories.core import CoreRepository
from chess_analytics.storage.repositories.players import PlayerRepository

from .factories import make_parsed_game, make_player, make_raw_game, parsed_moves

pytestmark = pytest.mark.integration

PARSER = 1


@pytest.fixture
def core(session: Session) -> CoreRepository:
    return CoreRepository(session)


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model))


# ------------------------------------------------------ players from games ---


def resolve(session: Session, uuid: str, username: str) -> Player:
    return PlayerRepository(session).resolve_from_game(
        platform="chesscom", platform_uuid=uuid, username=username
    )


def test_new_uuid_creates_a_player(session: Session) -> None:
    player = resolve(session, "uuid-1", "Opponent")
    assert (player.platform_uuid, player.username) == ("uuid-1", "Opponent")


def test_same_uuid_is_the_same_player_and_follows_renames(session: Session) -> None:
    first = resolve(session, "uuid-1", "old_name")
    again = resolve(session, "uuid-1", "new_name")
    assert again.id == first.id
    assert again.username == "new_name"


def test_placeholder_from_profile_gets_its_uuid(session: Session) -> None:
    # The synced player was created from their profile (no uuid). Their first game reveals it.
    placeholder = make_player(session, platform_uuid=None, username="Adarsxh1",
                              platform_player_id=42)  # fmt: skip
    player = resolve(session, "uuid-me", "adarsxh1")

    assert player.id == placeholder.id
    assert (player.platform_uuid, player.platform_player_id) == ("uuid-me", 42)
    assert count(session, Player) == 1


# ------------------------------------------------------------------ save ---


def test_save_game_writes_game_sides_and_moves_with_lineage(
    session: Session, core: CoreRepository
) -> None:
    raw = make_raw_game(session)
    parsed = make_parsed_game(
        white=ParsedPlayer(Color.WHITE, "uuid-w", "W", 1500, "win", Outcome.WIN, Decimal("81.25"))
    )

    game_id = core.save_game(parsed, raw_game_id=raw.id, parser_version=PARSER)

    game = session.get_one(Game, game_id)
    assert (game.raw_game_id, game.parser_version) == (raw.id, PARSER)
    assert (game.result, game.time_class, game.ply_count) == ("white_win", "rapid", 2)
    assert game.moves_hash == parsed.moves_hash

    sides = {gp.color: gp for gp in session.scalars(select(GamePlayer))}
    assert (sides["white"].outcome, sides["white"].result_code) == ("win", "win")
    assert sides["white"].source_accuracy == Decimal("81.25")
    assert (sides["black"].outcome, sides["black"].rating_after) == ("loss", 1480)
    white_player = session.get_one(Player, sides["white"].player_id)
    assert white_player.platform_uuid == "uuid-w"

    moves = session.scalars(select(GameMove).order_by(GameMove.ply)).all()
    assert [(m.ply, m.san, m.uci) for m in moves] == [(1, "e4", "e2e4"), (2, "e5", "e7e5")]


def test_reprocessing_keeps_the_game_id_and_replaces_children(
    session: Session, core: CoreRepository
) -> None:
    first_raw, second_raw = make_raw_game(session), make_raw_game(session)
    parsed = make_parsed_game(platform_game_id="g-1")
    game_id = core.save_game(parsed, raw_game_id=first_raw.id, parser_version=PARSER)

    # A newer raw version with a different (shorter) move list and result.
    changed = replace(parsed, moves=parsed_moves(("d4", "d2d4")), result=GameResult.DRAW,
                      moves_hash="changed")  # fmt: skip
    again = core.save_game(changed, raw_game_id=second_raw.id, parser_version=2)

    assert again == game_id  # same row: analyses pointing at it stay valid
    game = session.get_one(Game, game_id)
    session.refresh(game)
    assert (game.raw_game_id, game.parser_version, game.result) == (second_raw.id, 2, "draw")
    assert game.moves_hash == "changed"
    assert [m.san for m in session.scalars(select(GameMove))] == ["d4"]  # old moves gone
    assert count(session, GamePlayer) == 2  # sides replaced, not duplicated
    assert count(session, Game) == 1


def test_a_game_with_no_moves(session: Session, core: CoreRepository) -> None:
    raw = make_raw_game(session)
    core.save_game(make_parsed_game(moves=()), raw_game_id=raw.id, parser_version=PARSER)
    assert count(session, GameMove) == 0
    assert session.scalar(select(Game.ply_count)) == 0


# -------------------------------------------------------------- failures ---


def test_recording_a_failure_is_idempotent(session: Session, core: CoreRepository) -> None:
    raw = make_raw_game(session)
    core.record_failure(raw_game_id=raw.id, parser_version=PARSER, error="first")
    core.record_failure(raw_game_id=raw.id, parser_version=PARSER, error="second")

    failures = session.scalars(select(ProcessingFailure)).all()
    assert [(f.raw_game_id, f.error) for f in failures] == [(raw.id, "second")]


# ------------------------------------------------------- needs processing ---


def test_unprocessed_raw_game_is_pending(session: Session, core: CoreRepository) -> None:
    raw = make_raw_game(session)
    assert core.pending_raw_game_ids(parser_version=PARSER) == [raw.id]


def test_processed_game_is_not_pending(session: Session, core: CoreRepository) -> None:
    raw = make_raw_game(session)
    core.save_game(make_parsed_game(platform_game_id=raw.platform_game_id), raw_game_id=raw.id,
                   parser_version=PARSER)  # fmt: skip
    assert core.pending_raw_game_ids(parser_version=PARSER) == []


def test_a_newer_raw_version_makes_the_game_pending_again(
    session: Session, core: CoreRepository
) -> None:
    old = make_raw_game(session, platform_game_id="g-1")
    core.save_game(make_parsed_game(platform_game_id="g-1"), raw_game_id=old.id,
                   parser_version=PARSER)  # fmt: skip
    new = make_raw_game(session, platform_game_id="g-1", content_hash="v2")
    # The old version was last seen earlier → the new one is the current version.
    session.execute(
        update(RawGame)
        .where(RawGame.id == old.id)
        .values(last_seen_at=datetime(2026, 1, 1, tzinfo=UTC))
    )

    assert core.pending_raw_game_ids(parser_version=PARSER) == [new.id]


def test_a_new_parser_version_makes_processed_games_pending(
    session: Session, core: CoreRepository
) -> None:
    raw = make_raw_game(session)
    core.save_game(make_parsed_game(platform_game_id=raw.platform_game_id), raw_game_id=raw.id,
                   parser_version=1)  # fmt: skip
    assert core.pending_raw_game_ids(parser_version=1) == []
    assert core.pending_raw_game_ids(parser_version=2) == [raw.id]


def test_rejected_games_are_skipped_until_the_parser_changes(
    session: Session, core: CoreRepository
) -> None:
    raw = make_raw_game(session)
    core.record_failure(raw_game_id=raw.id, parser_version=1, error="unsupported rules: odds")
    assert core.pending_raw_game_ids(parser_version=1) == []
    assert core.pending_raw_game_ids(parser_version=2) == [raw.id]  # retried by a new parser


def test_pending_limit(session: Session, core: CoreRepository) -> None:
    ids = [make_raw_game(session).id for _ in range(3)]
    assert core.pending_raw_game_ids(parser_version=PARSER, limit=2) == ids[:2]
