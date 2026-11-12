"""`ops` schema: the Postgres-backed job queue (ADR-003, ADR-018, DATA_MODEL.md §4.4).

Deliberately generic: domain tables never reference jobs, so this queue could later be
replaced (Celery, a cloud queue) without breaking lineage.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, SmallInteger, func, text
from sqlalchemy.orm import Mapped, mapped_column

from chess_analytics.storage.schema import OPS, Base, IdMixin, TimestampsMixin, one_of

JOB_STATUSES = ("queued", "running", "succeeded", "failed")
ACTIVE_JOB_STATUSES = ("queued", "running")


class Job(IdMixin, TimestampsMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        one_of("job_type", ("sync_player", "analyse_game")),
        one_of("status", JOB_STATUSES),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        # A running job always holds a lease; otherwise a crashed worker's job could
        # never be detected as abandoned.
        CheckConstraint(
            "status <> 'running' OR locked_until IS NOT NULL", name="running_has_lease"
        ),
        # Deduplication: at most one ACTIVE job per key (finished jobs don't block a new one).
        Index(
            "uq_jobs_dedup_key_active",
            "dedup_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        # Claiming: "next queued job by priority, whose run_after has passed".
        Index(
            "ix_jobs_claimable",
            "priority",
            "run_after",
            postgresql_where=text("status = 'queued'"),
        ),
        # Recovery: "running jobs whose lease has expired".
        Index("ix_jobs_running_lease", "locked_until", postgresql_where=text("status = 'running'")),
        {"schema": OPS},
    )

    job_type: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(server_default="queued")
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="100")  # lower runs first
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    max_attempts: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())  # retry backoff
    locked_by: Mapped[str | None]  # worker identity holding the lease
    locked_until: Mapped[datetime | None]  # lease expiry; extended by heartbeats
    dedup_key: Mapped[str | None]  # e.g. "sync:chesscom:42", "analyse:1001:3"
    last_error: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
