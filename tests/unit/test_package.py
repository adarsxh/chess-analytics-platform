import chess_analytics


def test_package_is_installed_and_versioned() -> None:
    assert chess_analytics.__version__ == "0.1.0"
