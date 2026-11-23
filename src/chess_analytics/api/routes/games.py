"""Single-game endpoints: the game itself, and its engine analysis."""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status

from chess_analytics.analytics.accuracy import game_accuracy
from chess_analytics.api.dependencies import SessionDep, SessionFactoryDep
from chess_analytics.api.schemas import AnalysisRequestedResponse, GameAnalysisResponse, GameDetail
from chess_analytics.services.analyse import GameNotFoundError, request_analysis
from chess_analytics.storage.repositories.jobs import JobRepository
from chess_analytics.storage.repositories.queries import GameQueries

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/{game_id}", response_model=GameDetail, summary="One game with sides and moves")
def get_game(game_id: int, session: SessionDep, include_moves: bool = True) -> GameDetail:
    detail = GameQueries(session).game_detail(game_id, include_moves=include_moves)
    if detail is None:
        raise _game_not_found(game_id)
    return GameDetail(**detail)


@router.post(
    "/{game_id}/analysis",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisRequestedResponse,
    summary="Queue an engine analysis of the game",
)
def request_game_analysis(
    game_id: int, session_factory: SessionFactoryDep, session: SessionDep, response: Response
) -> AnalysisRequestedResponse:
    """Returns immediately with a job; poll `GET /api/v1/jobs/{job_id}`, then read
    `GET /api/v1/games/{game_id}/analysis`. An analysis already queued or running is
    returned instead (`created: false`) and, if it was background work, moved ahead.
    A game that is already analysed finishes almost at once (nothing to recompute)."""
    try:
        job_id, created = request_analysis(session_factory, game_id)
    except GameNotFoundError:
        raise _game_not_found(game_id) from None
    job = JobRepository(session).get(job_id)
    assert job is not None
    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    return AnalysisRequestedResponse.from_job(job, created=created)


@router.get(
    "/{game_id}/analysis",
    response_model=GameAnalysisResponse,
    summary="Engine evaluations and move labels",
)
def get_game_analysis(game_id: int, session: SessionDep) -> GameAnalysisResponse:
    queries = GameQueries(session)
    analysis = queries.game_analysis(game_id)
    if analysis is not None:
        return GameAnalysisResponse(**analysis, accuracy=_accuracy_by_side(analysis["moves"]))
    if not queries.game_exists(game_id):
        raise _game_not_found(game_id)
    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        detail=f"game {game_id} has not been analysed; POST to this URL to request it",
    )


def _accuracy_by_side(moves: list[dict[str, Any]]) -> dict[str, Decimal | None]:
    """The same game accuracy as the stats endpoints, for each side (forced moves excluded)."""
    if not moves:
        return {}
    result = {}
    for color in ("white", "black"):
        value = game_accuracy(
            float(m["win_loss"]) for m in moves if m["color"] == color and m["label"] != "forced"
        )
        result[color] = None if value is None else round(Decimal(value), 1)
    return result


def _game_not_found(game_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"game {game_id} not found")
