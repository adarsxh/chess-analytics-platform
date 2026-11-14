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
