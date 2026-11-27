"""Response models: the API's public contract (what clients such as the dashboard see).

Kept separate from the database models on purpose: tables can change shape without
breaking clients, and nothing internal leaks out (e.g. lineage ids, worker names).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, Field

from chess_analytics.storage.models.ops import Job

USERNAME_PATTERN = r"^[A-Za-z0-9_-]{1,50}$"  # Chess.com usernames

Platform = Literal["chesscom"]
TimeClass = Literal["bullet", "blitz", "rapid", "daily"]
Outcome = Literal["win", "loss", "draw"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


# ------------------------------------------------------------------- jobs ---


class JobResponse(BaseModel):
    job_id: int
    job_type: str
    status: JobStatus
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_job(cls, job: Job, **extra: Any) -> Self:
        """Build from a job row; `extra` fills fields a subclass adds (e.g. `created`)."""
        return cls(
            **extra,
            job_id=job.id,
            job_type=job.job_type,
            status=job.status,
            payload=job.payload,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            last_error=job.last_error,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


class SyncRequestedResponse(JobResponse):
    created: bool = Field(description="False if an identical sync was already queued or running")


class AnalysisRequestedResponse(JobResponse):
    created: bool = Field(
        description="False if an analysis of this game was already queued or running"
    )


class AnalyseAllRequestedResponse(JobResponse):
    created: bool = Field(
        description='False if an "Analyse all" of this player was already queued or running'
    )


class AnalysisProgressResponse(BaseModel):
    total_games: int
    analysed_games: int = Field(description="Games with a fresh analysis (what the stats use)")
    queued: int = Field(description="Game analyses waiting for a worker")
    running: int = Field(description="Game analyses in progress")
    expanding: bool = Field(
        description='An "Analyse all" request is still being turned into game analyses'
    )


class AnalyseAllCancelledResponse(BaseModel):
    cancelled: int = Field(description="Queued game analyses withdrawn")


# ---------------------------------------------------------------- players ---


class Record(BaseModel):
    games: int
    wins: int
    losses: int
    draws: int


class PlayerResponse(BaseModel):
    platform: Platform
    username: str
    platform_uuid: str | None
    tracked: bool
    last_synced_at: datetime | None
    record: Record


# ------------------------------------------------------------------ games ---


class GameSummary(BaseModel):
    """One game from the requested player's point of view."""

    game_id: int
    platform_game_id: str
    url: str
    end_time: datetime
    time_class: TimeClass
    time_control: str
    rated: bool
    ply_count: int
    eco: str | None
    opening_name: str | None = Field(description="Approximate, from Chess.com's opening URL")
    color: Literal["white", "black"]
    outcome: Outcome
    result_code: str
    rating_after: int | None
    accuracy: Decimal | None
    opponent_username: str
    opponent_rating_after: int | None


class GamePage(BaseModel):
    items: list[GameSummary]
    total: int
    limit: int
    offset: int


class Side(BaseModel):
    username: str
    rating_after: int | None
    outcome: Outcome
    result_code: str
    accuracy: Decimal | None


class Move(BaseModel):
    ply: int
    san: str
    uci: str
    fen_after: str
    clock_ms: int | None
    time_spent_ms: int | None


class GameDetail(BaseModel):
    game_id: int
    platform: Platform
    platform_game_id: str
    url: str
    rules: str
    time_class: TimeClass
    time_control: str
    rated: bool
    start_time: datetime | None
    end_time: datetime
    result: Literal["white_win", "black_win", "draw"]
    termination: str | None
    eco: str | None
    opening_name: str | None
    initial_fen: str
    final_fen: str
    ply_count: int
    white: Side
    black: Side
    moves: list[Move]


# --------------------------------------------------------------- analysis ---

MoveLabel = Literal["forced", "best", "excellent", "good", "inaccuracy", "mistake", "blunder"]


class EngineConfig(BaseModel):
    name: str
    version: str
    nodes_per_position: int
    threads: int
    hash_mb: int
    multipv: int


class EvaluatedPosition(BaseModel):
    """The engine's view of the position after `ply` moves (0 = the start position)."""

    ply: int
    score_cp: int | None = Field(description="White's point of view; null when mate_in is set")
    mate_in: int | None = Field(
        description="Moves to mate, + for White, − for Black; 0 = the side to move is mated"
    )
    best_move_uci: str | None = Field(description="Null when the game is over")
    pv_uci: list[str]
    depth: int


class ClassifiedMove(BaseModel):
    ply: int
    color: Literal["white", "black"] = Field(description="Who made the move")
    san: str
    uci: str
    label: MoveLabel
    win_loss: Decimal = Field(description="The mover's win-% loss, 0–100 (ADR-024)")


class GameAnalysisResponse(BaseModel):
    game_id: int
    analysis_id: int
    status: Literal["pending", "running", "succeeded", "failed"]
    fresh: bool = Field(description="False if the game's moves changed after this analysis")
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    engine: EngineConfig
    classifier_version: int | None
    accuracy: dict[Literal["white", "black"], Decimal | None] = Field(
        description="Each side's game accuracy (ADR-026); empty unless status is succeeded"
    )
    positions: list[EvaluatedPosition] = Field(description="Empty unless status is succeeded")
    moves: list[ClassifiedMove] = Field(description="Empty unless status is succeeded")


# ------------------------------------------------------------------ stats ---


class ResultRecord(Record):
    score_percent: Decimal | None = Field(
        description="Points per game in %: win 1, draw ½. Null without games"
    )


class OverviewStats(BaseModel):
    total: ResultRecord
    by_time_class: dict[TimeClass, ResultRecord]
    by_color: dict[Literal["white", "black"], ResultRecord]


class RatingPoint(BaseModel):
    time_class: TimeClass
    day: date
    rating: int = Field(description="After the last rated game of the day")
    games: int = Field(description="Rated games that day")


class OpeningStats(ResultRecord):
    opening: str = Field(description="Family (default) or full name, from Chess.com")
    color: Literal["white", "black"]
    eco: str | None = Field(description="Most common ECO code in the group")


class Coverage(BaseModel):
    analysed_games: int
    total_games: int = Field(description="Games matching the filters")
    percent: Decimal | None


class QualitySummary(BaseModel):
    accuracy: Decimal | None = Field(description="Mean of game accuracies (ADR-026)")
    inaccuracies_per_game: Decimal | None
    mistakes_per_game: Decimal | None
    blunders_per_game: Decimal | None


class GameQuality(BaseModel):
    game_id: int
    end_time: datetime
    time_class: TimeClass
    color: Literal["white", "black"]
    outcome: Outcome
    accuracy: Decimal | None = Field(description="Ours: (mean + harmonic mean) / 2 (ADR-026)")
    source_accuracy: Decimal | None = Field(description="Chess.com's, when it provides one")
    moves: int
    inaccuracies: int
    mistakes: int
    blunders: int


class AccuracyStats(BaseModel):
    coverage: Coverage
    summary: QualitySummary
    games: list[GameQuality] = Field(description="Analysed games, newest first")
