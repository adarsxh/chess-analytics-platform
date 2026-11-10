"""Application configuration, loaded from environment variables and an optional `.env` file.

Precedence (highest first): real environment variables > `.env` file > defaults below.
All variables use the `CHESS_` prefix, e.g. `CHESS_LOG_LEVEL=DEBUG`.

Settings are added here only when the component that needs them is built.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, built once on first call."""
    return Settings()
