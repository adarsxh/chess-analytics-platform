"""Health endpoints for Docker, CI, load balancers and humans.

- `GET /health`        liveness: the process is running and serving requests.
                       Never touches dependencies, so a DB outage doesn't get the API
                       restarted for no reason.
- `GET /health/ready`  readiness: the API can do useful work (database reachable).
                       Returns 503 when not ready, so traffic can be held back.
"""

from typing import Literal

import structlog
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from chess_analytics import __version__
from chess_analytics.api.dependencies import EngineDep, SettingsDep
from chess_analytics.storage.db import check_connection

router = APIRouter(prefix="/health", tags=["health"])
log = structlog.get_logger(__name__)

CheckStatus = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    env: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckStatus]


@router.get("")
def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, env=settings.env)


@router.get("/ready", responses={503: {"model": ReadinessResponse}})
def ready(engine: EngineDep, response: Response) -> ReadinessResponse:
    try:
        check_connection(engine)
        database: CheckStatus = "ok"
    except SQLAlchemyError as exc:
        # Log the cause for operators; don't leak connection details to HTTP clients.
        cause = getattr(exc, "orig", None) or exc  # the underlying driver error, if any
        log.warning("readiness_check_failed", check="database", error=str(cause))
        database = "unavailable"

    if database == "ok":
        return ReadinessResponse(status="ready", checks={"database": database})
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="not_ready", checks={"database": database})
