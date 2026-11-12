"""Each test proves one guarantee from DATA_MODEL.md §5, enforced by the database itself.

If one of these fails after a migration, a guarantee the rest of the system relies on is gone.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from chess_analytics.storage.models.analysis import MoveClassification, PositionEvaluation
from chess_analytics.storage.models.core import GameMove, GamePlayer, Player
from chess_analytics.storage.models.ops import Job
from chess_analytics.storage.models.raw import RawGame

from .factories import (
    add,
    make_analysis_config,
    make_archive_fetch,
    make_game,
    make_game_analysis,
    make_player,
    make_raw_game,
    violates,
)

pytestmark = pytest.mark.integration


# ------------------------------------------------- identity & deduplication ---


def test_a_game_is_stored_once_per_platform_id(session: Session) -> None:
    game = make_game(session)
    with violates(session, "uq_games_platform_platform_game_id"):
        make_game(session, platform_game_id=game.platform_game_id)


def test_a_player_identity_is_stored_once(session: Session) -> None:
    player = make_player(session)
    with violates(session, "uq_players_platform_platform_uuid"):
        make_player(session, platform_uuid=player.platform_uuid)


def test_one_placeholder_per_username_case_insensitive(session: Session) -> None:
    make_player(session, platform_uuid=None, username="Adarsxh1")
    with violates(session, "uq_players_platform_lower_username_no_uuid"):
        make_player(session, platform_uuid=None, username="adarsxh1")


def test_known_player_and_placeholder_may_share_a_username(session: Session) -> None:
    # Rename scenario: an old identity still holds the name; a new lookup creates a placeholder.
    make_player(session, username="taken_name")
    make_player(session, platform_uuid=None, username="taken_name")


def test_a_game_has_one_player_per_color_and_distinct_players(session: Session) -> None:
    game, alice, bob = make_game(session), make_player(session), make_player(session)
    side = {"result_code": "win", "outcome": "win"}
    add(session, GamePlayer(game_id=game.id, color="white", player_id=alice.id, **side))

    with violates(session, "pk_game_players"):
        add(session, GamePlayer(game_id=game.id, color="white", player_id=bob.id, **side))
    with violates(session, "uq_game_players_game_id_player_id"):
        add(session, GamePlayer(game_id=game.id, color="black", player_id=alice.id, **side))


# ---------------------------------------------------- raw layer versioning ---


def test_identical_raw_content_is_one_version(session: Session) -> None:
    raw = make_raw_game(session)
    with violates(session, "uq_raw_games_platform_platform_game_id_content_hash"):
        make_raw_game(session, platform_game_id=raw.platform_game_id, content_hash=raw.content_hash)


def test_changed_raw_content_becomes_a_new_version(session: Session) -> None:
    raw = make_raw_game(session)
    make_raw_game(session, platform_game_id=raw.platform_game_id, content_hash="different")

    versions = session.scalar(
        select(func.count()).where(RawGame.platform_game_id == raw.platform_game_id)
    )
    assert versions == 2


def test_refetching_a_version_is_idempotent_and_bumps_last_seen(session: Session) -> None:
    # The write pattern ingestion will use: INSERT ... ON CONFLICT DO UPDATE last_seen_at.
    fetch_id = make_archive_fetch(session).id
    row = {
        "platform": "chesscom",
        "platform_game_id": "g-1",
        "content_hash": "h-1",
        "payload": {"uuid": "g-1"},
        "source_fetch_id": fetch_id,
    }
    first_seen = datetime(2026, 10, 1, tzinfo=UTC)
    seen_again = datetime(2026, 11, 1, tzinfo=UTC)
    for seen_at in (first_seen, seen_again):
        statement = insert(RawGame).values(**row, last_seen_at=seen_at)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["platform", "platform_game_id", "content_hash"],
                set_={"last_seen_at": statement.excluded.last_seen_at},
            )
        )

    stored = session.scalars(select(RawGame).where(RawGame.platform_game_id == "g-1")).all()
    assert len(stored) == 1
    assert stored[0].last_seen_at == seen_again


# --------------------------------------------------- lineage & deletion rules ---


def test_raw_data_used_by_a_game_cannot_be_deleted(session: Session) -> None:
    game = make_game(session)
    with violates(session, "fk_games_raw_game_id_raw_games"):
        session.execute(delete(RawGame).where(RawGame.id == game.raw_game_id))


def test_deleting_a_game_removes_its_own_players_and_moves(session: Session) -> None:
    game, player = make_game(session), make_player(session)
    add(session, GamePlayer(game_id=game.id, color="white", player_id=player.id,
                            result_code="win", outcome="win"))  # fmt: skip
    add(session, GameMove(game_id=game.id, ply=1, san="e4", uci="e2e4", fen_after="x"))

    session.delete(game)
    session.flush()

    assert session.scalar(select(func.count()).select_from(GamePlayer)) == 0
    assert session.scalar(select(func.count()).select_from(GameMove)) == 0
    assert session.get(Player, player.id) is not None  # players are not the game's children


def test_engine_results_block_deleting_their_game(session: Session) -> None:
    game = make_game(session)
    make_game_analysis(session, game)
    with violates(session, "fk_game_analyses_game_id_games"):
        session.delete(game)


# ------------------------------------------------------ analysis versioning ---


def test_a_new_engine_config_adds_an_analysis_alongside_the_old(session: Session) -> None:
    game = make_game(session)
    old = make_game_analysis(session, game)
    new = make_game_analysis(session, game)  # factory creates a second, different config
    assert old.id != new.id


def test_same_config_cannot_analyse_a_game_twice(session: Session) -> None:
    game = make_game(session)
    first = make_game_analysis(session, game)
    with violates(session, "uq_game_analyses_game_id_analysis_config_id"):
        make_game_analysis(session, game, analysis_config_id=first.analysis_config_id)


def test_identical_engine_settings_share_one_config(session: Session) -> None:
    config = make_analysis_config(session)
    with violates(session, "uq_analysis_configs_config_hash"):
        make_analysis_config(session, config_hash=config.config_hash)


@pytest.mark.parametrize(
    ("score_cp", "mate_in", "valid"),
    [(35, None, True), (None, 3, True), (35, 3, False), (None, None, False)],
)
def test_an_evaluation_is_exactly_one_of_centipawns_or_mate(
    session: Session, score_cp: int | None, mate_in: int | None, valid: bool
) -> None:
    analysis = make_game_analysis(session, make_game(session))
    evaluation = PositionEvaluation(
        game_analysis_id=analysis.id, ply=0, score_cp=score_cp, mate_in=mate_in,
        depth_reached=20, nodes_searched=1_000_000,
    )  # fmt: skip
    if valid:
        add(session, evaluation)
    else:
        with violates(session, "ck_position_evaluations_exactly_one_of_score_cp_mate_in"):
            add(session, evaluation)


def test_reclassification_adds_a_version_and_removal_cascades(session: Session) -> None:
    analysis = make_game_analysis(session, make_game(session))
    for version in (1, 2):
        add(session, MoveClassification(game_analysis_id=analysis.id, ply=1,
                                        classifier_version=version, label="mistake",
                                        eval_loss=120))  # fmt: skip
    assert session.scalar(select(func.count()).select_from(MoveClassification)) == 2

    session.delete(analysis)
    session.flush()
    assert session.scalar(select(func.count()).select_from(MoveClassification)) == 0


# ------------------------------------------------------------------ jobs ---


def test_only_one_active_job_per_dedup_key(session: Session) -> None:
    first = add(session, Job(job_type="sync_player", dedup_key="sync:chesscom:1"))
    with violates(session, "uq_jobs_dedup_key_active"):
        add(session, Job(job_type="sync_player", dedup_key="sync:chesscom:1"))

    # Once finished, the same work can be requested again.
    first.status = "succeeded"
    session.flush()
    add(session, Job(job_type="sync_player", dedup_key="sync:chesscom:1"))


def test_a_running_job_must_hold_a_lease(session: Session) -> None:
    with violates(session, "ck_jobs_running_has_lease"):
        add(session, Job(job_type="analyse_game", status="running"))


# --------------------------------------------------------- allowed values ---


@pytest.mark.parametrize(
    ("factory_kwargs", "constraint"),
    [
        ({"result": "white_wins"}, "ck_games_result"),
        ({"time_class": "classical"}, "ck_games_time_class"),
        ({"platform": "lichess"}, "ck_games_platform"),
    ],
)
def test_fixed_value_sets_are_enforced(
    session: Session, factory_kwargs: dict, constraint: str
) -> None:
    with violates(session, constraint):
        make_game(session, **factory_kwargs)
