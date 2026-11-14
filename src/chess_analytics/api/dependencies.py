"""Reusable FastAPI dependencies, injected into route handlers with `Depends(...)`."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The settings this app instance was created with (see `create_app`)."""
    return request.app.state.settings


def get_engine(request: Request) -> Engine:
    """The database engine created in the app lifespan."""
    return request.app.state.engine


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """For handlers that run a service owning its own transactions."""
    return request.app.state.session_factory


def get_session(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Iterator[Session]:
    """One session per request, closed afterwards (read-only handlers)."""
    with factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]
SessionFactoryDep = Annotated[sessionmaker[Session], Depends(get_session_factory)]
SessionDep = Annotated[Session, Depends(get_session)]
