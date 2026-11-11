"""Reusable FastAPI dependencies, injected into route handlers with `Depends(...)`."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine

from chess_analytics.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The settings this app instance was created with (see `create_app`)."""
    return request.app.state.settings


def get_engine(request: Request) -> Engine:
    """The database engine created in the app lifespan."""
    return request.app.state.engine


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]
