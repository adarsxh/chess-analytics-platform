import pytest
from pydantic import ValidationError

from chess_analytics.config import Settings


def make_settings(**kwargs) -> Settings:
    # _env_file=None: tests must not depend on whatever is in a developer's local .env
    return Settings(_env_file=None, **kwargs)


def test_defaults() -> None:
    settings = make_settings()
    assert settings.env == "local"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"


def test_reads_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_LOG_LEVEL", "debug")
    monkeypatch.setenv("CHESS_LOG_FORMAT", "console")
    settings = make_settings()
    assert settings.log_level == "DEBUG"  # normalised to upper case
    assert settings.log_format == "console"


def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_LOG_FORMAT", "xml")
    with pytest.raises(ValidationError):
        make_settings()


def test_rejects_unknown_prefixed_keys_in_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CHESS_LOG_LEVL=DEBUG\n")  # typo
    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)
