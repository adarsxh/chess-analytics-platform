"""Application configuration, loaded from environment variables and an optional `.env` file.

Precedence (highest first): real environment variables > `.env` file > defaults below.
All variables use the `CHESS_` prefix, e.g. `CHESS_LOG_LEVEL=DEBUG`.

Settings are added here only when the component that needs them is built.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        # Unknown CHESS_* keys (e.g. a typo like CHESS_LOG_LEVL) fail loudly at startup.
        extra="forbid",
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

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, built once on first call."""
    return Settings()
