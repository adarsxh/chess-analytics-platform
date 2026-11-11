"""Reusable FastAPI dependencies, injected into route handlers with `Depends(...)`."""

from typing import Annotated

from fastapi import Depends, Request

from chess_analytics.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The settings this app instance was created with (see `create_app`)."""
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
