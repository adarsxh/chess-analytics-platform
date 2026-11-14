"""Player endpoints: request a sync, read a profile, list games."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from chess_analytics.api.dependencies import SessionDep, SessionFactoryDep
from chess_analytics.api.schemas import (
    USERNAME_PATTERN,
    GamePage,
    Outcome,
    Platform,
    PlayerResponse,
    SyncRequestedResponse,
    TimeClass,
)
from chess_analytics.services.sync import request_sync
from chess_analytics.storage.models.core import Player
from chess_analytics.storage.repositories.jobs import JobRepository
from chess_analytics.storage.repositories.queries import GameQueries

router = APIRouter(prefix="/players", tags=["players"])

Username = Annotated[str, Path(pattern=USERNAME_PATTERN, description="Case-insensitive")]
PlatformParam = Annotated[Platform, Query()]


@router.post(
    "/{username}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncRequestedResponse,
    summary="Queue a sync of the player's games",
)
def request_player_sync(
    username: Username,
    session_factory: SessionFactoryDep,
    session: SessionDep,
    response: Response,
    platform: PlatformParam = "chesscom",
) -> SyncRequestedResponse:
    """Returns immediately with a job; poll `GET /api/v1/jobs/{job_id}` for progress.
    Requesting a sync that is already queued or running returns that job (`created: false`)."""
    job_id, created = request_sync(session_factory, platform=platform, username=username)
    job = JobRepository(session).get(job_id)
    assert job is not None
    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    return SyncRequestedResponse.from_job(job, created=created)


@router.get("/{username}", response_model=PlayerResponse, summary="Player profile and record")
def get_player(
    username: Username, session: SessionDep, platform: PlatformParam = "chesscom"
) -> PlayerResponse:
    queries = GameQueries(session)
    return PlayerResponse(**queries.player_summary(_player_or_404(queries, platform, username)))


@router.get(
    "/{username}/games", response_model=GamePage, summary="The player's games, newest first"
)
def list_player_games(
    username: Username,
    session: SessionDep,
    platform: PlatformParam = "chesscom",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    time_class: TimeClass | None = None,
    outcome: Outcome | None = None,
    rated: bool | None = None,
) -> GamePage:
    queries = GameQueries(session)
    player = _player_or_404(queries, platform, username)
    items, total = queries.list_player_games(
        player.id, limit=limit, offset=offset, time_class=time_class, outcome=outcome, rated=rated
    )
    return GamePage(items=items, total=total, limit=limit, offset=offset)


def _player_or_404(queries: GameQueries, platform: str, username: str) -> Player:
    player = queries.find_player(platform=platform, username=username)
    if player is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"player {username!r} not found; request a sync first",
        )
    return player
