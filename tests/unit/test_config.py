import pytest
from pydantic import ValidationError

from chess_analytics.config import Settings

DB_URL = "postgresql+psycopg://user:secret@localhost:5432/db"


def make_settings(**kwargs) -> Settings:
    # _env_file=None: tests must not depend on whatever is in a developer's local .env
    return Settings(_env_file=None, **kwargs)


def test_defaults() -> None:
    settings = make_settings(database_url=DB_URL)
    assert settings.env == "local"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_reload is False


def test_database_url_is_required() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        make_settings()


def test_database_password_is_hidden_from_repr() -> None:
    settings = make_settings(database_url=DB_URL)
    assert "secret" not in repr(settings)
    assert settings.database_url.get_secret_value() == DB_URL


def test_reads_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_LOG_LEVEL", "debug")
    monkeypatch.setenv("CHESS_LOG_FORMAT", "console")
    monkeypatch.setenv("CHESS_DATABASE_URL", DB_URL)
    settings = make_settings()
    assert settings.log_level == "DEBUG"  # normalised to upper case
    assert settings.log_format == "console"


def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_LOG_FORMAT", "xml")
    with pytest.raises(ValidationError):
        make_settings(database_url=DB_URL)


def test_rejects_invalid_values_in_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"CHESS_DATABASE_URL={DB_URL}\nCHESS_API_PORT=not-a-number\n")
    with pytest.raises(ValidationError, match="api_port"):
        Settings(_env_file=env_file)


def test_ignores_unrelated_keys_in_env_file(tmp_path) -> None:
    # .env also holds POSTGRES_* values for docker compose; the app must ignore them.
    env_file = tmp_path / ".env"
    env_file.write_text(f"CHESS_DATABASE_URL={DB_URL}\nPOSTGRES_PASSWORD=x\n")
    assert Settings(_env_file=env_file).database_url.get_secret_value() == DB_URL


def test_default_chesscom_user_agent_names_the_project_without_personal_details() -> None:
    agent = make_settings(database_url=DB_URL).chesscom_user_agent
    assert "github.com/adarsxh/chess-analytics-platform" in agent
    assert "@" not in agent  # contact details are opt-in via the user's own .env
