"""Health endpoint: tells Docker, CI and humans whether the API process is alive.

This is a *liveness* check: it only proves the process is running and serving requests.
A *readiness* check (can we reach the database?) is added together with Postgres.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from chess_analytics import __version__
from chess_analytics.api.dependencies import SettingsDep

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    env: str


@router.get("/health")
def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, env=settings.env)
