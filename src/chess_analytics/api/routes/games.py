"""Single-game endpoint: both sides and (optionally) every move."""

from fastapi import APIRouter, HTTPException, status

from chess_analytics.api.dependencies import SessionDep
from chess_analytics.api.schemas import GameDetail
from chess_analytics.storage.repositories.queries import GameQueries

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/{game_id}", response_model=GameDetail, summary="One game with sides and moves")
def get_game(game_id: int, session: SessionDep, include_moves: bool = True) -> GameDetail:
    detail = GameQueries(session).game_detail(game_id, include_moves=include_moves)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"game {game_id} not found")
    return GameDetail(**detail)
