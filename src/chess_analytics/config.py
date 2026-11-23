"""Application configuration, loaded from environment variables and an optional `.env` file.

Precedence (highest first): real environment variables > `.env` file > defaults below.
All variables use the `CHESS_` prefix, e.g. `CHESS_LOG_LEVEL=DEBUG`.

Settings are added here only when the component that needs them is built.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        # .env is shared with docker compose (POSTGRES_* keys), so unknown keys must be
        # ignored. Trade-off: a typo like CHESS_LOG_LEVL is silently ignored too.
        # Invalid *values* for known settings still fail at startup.
        extra="ignore",
    )

    env: Literal["local", "test", "ci", "production"] = "local"
    log_level: LogLevel = "INFO"
    # Default to JSON: the safe choice for containers and production.
    # Developers opt into "console" via their local .env.
    log_format: Literal["console", "json"] = "json"

    # API server. 127.0.0.1 = reachable only from this machine (safe default);
    # containers override this with 0.0.0.0 so traffic from outside the container gets in.
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_reload: bool = False  # auto-restart on code changes; local development only

    # SQLAlchemy URL, e.g. postgresql+psycopg://user:password@localhost:5432/chess_analytics
    # Required: there is no safe default for credentials. SecretStr keeps the password
    # out of logs and reprs; call .get_secret_value() only where the URL is actually used.
    database_url: SecretStr

    # Chess.com public API. Chess.com asks clients to identify themselves with a
    # User-Agent that includes a way to contact the maintainer. The default points to
    # the project repository; add personal contact details only in your own .env.
    chesscom_base_url: str = "https://api.chess.com/pub"
    chesscom_user_agent: str = (
        "chess-analytics-platform/0.1 (+https://github.com/adarsxh/chess-analytics-platform)"
    )
    chesscom_timeout_seconds: float = Field(default=15.0, gt=0)

    # Worker (python -m chess_analytics.worker). The lease is how long a job stays
    # reserved without a heartbeat; heartbeats are sent every lease / 3.
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    worker_lease_seconds: int = Field(default=60, ge=10)

    # Stockfish (ADR-004). Changing nodes or hash creates a new analysis config, so results
    # from different settings are never mixed. Threads is fixed at 1 for reproducibility.
    # 300k nodes ≈ depth 18, ≈ 0.6 s per position on an Apple M-series core.
    engine_path: str = "stockfish"  # a command on PATH, or an absolute path
    engine_nodes_per_position: int = Field(default=300_000, gt=0)
    engine_hash_mb: int = Field(default=64, ge=1)
    # After each successful sync, the worker queues analysis of the player's N newest games
    # that aren't analysed yet, as background work (ADR-004). 0 turns this off.
    auto_analyse_latest_n: int = Field(default=20, ge=0)

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, built once on first call."""
    return Settings()
