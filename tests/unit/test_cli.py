"""The CLI's argument contract. Behaviour is tested at the service level."""

import pytest

from chess_analytics.cli import build_parser


def test_sync_defaults_to_incremental() -> None:
    args = build_parser().parse_args(["sync", "SomeOne"])
    assert (args.command, args.username, args.full_refresh) == ("sync", "SomeOne", False)


def test_sync_full_refresh_flag() -> None:
    assert build_parser().parse_args(["sync", "x", "--full-refresh"]).full_refresh is True


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([])
    assert exit_info.value.code == 2


def test_analyse_game_ids() -> None:
    args = build_parser().parse_args(["analyse", "5", "7"])
    assert (args.command, args.game_ids, args.player) == ("analyse", [5, 7], None)


def test_analyse_a_players_latest_games() -> None:
    args = build_parser().parse_args(["analyse", "--player", "SomeOne", "--latest", "5"])
    assert (args.game_ids, args.player, args.latest) == ([], "SomeOne", 5)
    assert build_parser().parse_args(["analyse", "--player", "x"]).latest == 20


@pytest.mark.parametrize(
    "argv",
    [
        ["analyse"],  # nothing to analyse
        ["analyse", "1", "--player", "x"],  # ids and a player are exclusive
        ["analyse", "--player", "x", "--latest", "0"],
        ["analyse", "not-a-number"],
    ],
)
def test_invalid_analyse_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)
    assert exit_info.value.code == 2
