"""Player statistics (ADR-026): results, rating history, openings, accuracy.

Every endpoint takes the same filters, so a dashboard showing several of them describes
one consistent set of games. Accuracy and error statistics include their coverage: how
many of the filtered games have been analysed (ADR-004).
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from chess_analytics.analytics.queries import Filters, OpeningGrouping, PlayerAnalytics
from chess_analytics.api.dependencies import SessionDep
from chess_analytics.api.routes.players import PlatformParam, Username, player_or_404
from chess_analytics.api.schemas import (
    AccuracyStats,
    OpeningStats,
    OverviewStats,
    RatingPoint,
    TimeClass,
)
from chess_analytics.storage.repositories.queries import GameQueries

router = APIRouter(prefix="/players/{username}/stats", tags=["stats"])


def stats_filters(
    time_class: TimeClass | None = None,
    since: Annotated[date | None, Query(description="First day included (UTC)")] = None,
    until: Annotated[date | None, Query(description="Last day included (UTC)")] = None,
) -> Filters:
    if since is not None and until is not None and since > until:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="`since` must not be after `until`"
        )
    return Filters(time_class=time_class, since=since, until=until)


FiltersDep = Annotated[Filters, Depends(stats_filters)]


def _player_id(session: SessionDep, username: str, platform: str) -> int:
    return player_or_404(GameQueries(session), platform, username).id


@router.get("/overview", response_model=OverviewStats, summary="Wins, draws and losses")
def overview(
    username: Username, session: SessionDep, filters: FiltersDep,
    platform: PlatformParam = "chesscom",
) -> OverviewStats:  # fmt: skip
    player_id = _player_id(session, username, platform)
    return OverviewStats(**PlayerAnalytics(session).overview(player_id, filters))


@router.get(
    "/rating-history",
    response_model=list[RatingPoint],
    summary="Rating after the last rated game of each day, per time class",
)
def rating_history(
    username: Username, session: SessionDep, filters: FiltersDep,
    platform: PlatformParam = "chesscom",
) -> list[RatingPoint]:  # fmt: skip
    player_id = _player_id(session, username, platform)
    return [RatingPoint(**p) for p in PlayerAnalytics(session).rating_history(player_id, filters)]


@router.get(
    "/openings", response_model=list[OpeningStats], summary="Most played openings per colour"
)
def openings(
    username: Username,
    session: SessionDep,
    filters: FiltersDep,
    platform: PlatformParam = "chesscom",
    group_by: Annotated[
        OpeningGrouping, Query(description="`family` (e.g. Scotch Game) or full `name`")
    ] = "family",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[OpeningStats]:
    player_id = _player_id(session, username, platform)
    rows = PlayerAnalytics(session).openings(player_id, filters, group_by=group_by, limit=limit)
    return [OpeningStats(**row) for row in rows]


@router.get(
    "/accuracy",
    response_model=AccuracyStats,
    summary="Accuracy and errors per analysed game, with coverage",
)
def accuracy(
    username: Username, session: SessionDep, filters: FiltersDep,
    platform: PlatformParam = "chesscom",
) -> AccuracyStats:  # fmt: skip
    player_id = _player_id(session, username, platform)
    return AccuracyStats(**PlayerAnalytics(session).game_quality(player_id, filters))
