"""The canonical content hash decides what counts as "a new version" of a raw game."""

from chess_analytics.storage.repositories.raw import content_hash


def test_key_order_does_not_change_the_hash() -> None:
    a = {"uuid": "g1", "white": {"username": "x", "rating": 1500}, "rules": "chess"}
    b = {"rules": "chess", "white": {"rating": 1500, "username": "x"}, "uuid": "g1"}
    assert content_hash(a) == content_hash(b)


def test_any_value_change_changes_the_hash() -> None:
    before = {"uuid": "g1", "accuracies": {"white": 81.2, "black": 77.0}}
    after = {"uuid": "g1", "accuracies": {"white": 81.3, "black": 77.0}}
    assert content_hash(before) != content_hash(after)


def test_hash_is_stable_across_runs() -> None:
    # A fixed expected value: if serialisation ever changes (separators, unicode handling),
    # every stored game would look "new" on the next sync. This test catches that.
    assert content_hash({"b": [1, 2], "a": "é"}) == (
        "9cfb1f938a87f2b8f3b8cc429c7a09116d54f048322742d4c23d4767b85f85da"
    )
