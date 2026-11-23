"""Postgres-backed job queue (ADR-003, ADR-018, DATA_MODEL.md §4.4).

Lifecycle:  queued ──claim──▶ running ──complete──▶ succeeded
                ▲                │
                └──retryable fail┤ (back to queued, run_after = now() + backoff)
                                 └──final fail / out of attempts──▶ failed

Concurrency rules, enforced in SQL rather than in Python:
- claim uses FOR UPDATE SKIP LOCKED: concurrent workers never take the same job and
  never wait for each other;
- a running job holds a lease (locked_until). Workers extend it with heartbeats; a job
  whose lease expired belonged to a crashed worker and is recovered;
- heartbeat/complete/fail only apply while *this* worker still holds the job, so a
  worker that lost its lease can't overwrite the job's new owner;
- all times come from the database clock (now()), never from worker machines.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from chess_analytics.storage.models.ops import ACTIVE_JOB_PREDICATE, ACTIVE_JOB_STATUSES, Job

RETRY_BASE_SECONDS = 30  # retry delays: 30 s, 2 min, 8 min, ... (x4 per attempt)

# Lower runs first. Work someone is waiting for (a sync, analysing one game) goes ahead
# of background work (auto-analysis after a sync).
PRIORITY_ON_DEMAND = 100
PRIORITY_BACKGROUND = 200


def retry_delay(attempt: int) -> timedelta:
    """Backoff before the next attempt, after `attempt` attempts have failed."""
    return timedelta(seconds=RETRY_BASE_SECONDS * 4 ** (attempt - 1))


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, job_id: int) -> Job | None:
        return self._session.get(Job, job_id)

    # ---------------------------------------------------------------- enqueue ---

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        dedup_key: str | None = None,
        priority: int = PRIORITY_ON_DEMAND,
        max_attempts: int = 3,
    ) -> tuple[Job, bool]:
        """Queue a job. Returns (job, created). With a dedup_key, an already queued or
        running job for the same key is returned instead of creating a duplicate; if it
        is still queued at a lower priority, it is moved up to the requested one (someone
        is now waiting for background work)."""
        statement = (
            insert(Job)
            .values(
                job_type=job_type,
                payload=payload,
                dedup_key=dedup_key,
                priority=priority,
                max_attempts=max_attempts,
            )
            .on_conflict_do_nothing(
                # Matches the partial unique index uq_jobs_dedup_key_active exactly
                # (literal SQL, not bind parameters: see ACTIVE_JOB_PREDICATE).
                index_elements=["dedup_key"],
                index_where=text(ACTIVE_JOB_PREDICATE),
            )
            .returning(Job.id)
        )
        new_id = self._session.execute(statement).scalar_one_or_none()
        if new_id is not None:
            return self._session.get_one(Job, new_id), True

        existing = self._session.scalar(
            select(Job).where(Job.dedup_key == dedup_key, Job.status.in_(ACTIVE_JOB_STATUSES))
        )
        if existing is None:  # it finished between our INSERT and SELECT: try once more
            return self.enqueue(job_type, payload, dedup_key=dedup_key, priority=priority,
                                max_attempts=max_attempts)  # fmt: skip
        # In SQL, not on the loaded object: a worker may claim the job at any moment, and
        # only a job that is still queued (and lower priority) may be moved up.
        moved_up = self._session.execute(
            update(Job)
            .where(Job.id == existing.id, Job.status == "queued", Job.priority > priority)
            .values(priority=priority, updated_at=func.now())
            .execution_options(synchronize_session=False)
        ).rowcount
        if moved_up:
            self._session.refresh(existing)
        return existing, False

    # ------------------------------------------------------------------ claim ---

    def claim(self, worker_id: str, *, lease: timedelta) -> Job | None:
        """Take the next runnable job (lowest priority number, then oldest), or None."""
        next_job = (
            select(Job.id)
            .where(Job.status == "queued", Job.run_after <= func.now())
            .order_by(Job.priority, Job.run_after, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        statement = (
            update(Job)
            .where(Job.id == next_job)
            .values(
                status="running",
                attempts=Job.attempts + 1,
                locked_by=worker_id,
                locked_until=func.now() + lease,
                started_at=func.now(),
                updated_at=func.now(),
            )
            .returning(Job)
            .execution_options(synchronize_session=False)
        )
        return self._session.execute(statement).scalar_one_or_none()

    # ------------------------------------------------------- while running ---

    def heartbeat(self, job_id: int, worker_id: str, *, lease: timedelta) -> bool:
        """Extend the lease. False means this worker no longer holds the job."""
        return self._update_held(job_id, worker_id, locked_until=func.now() + lease)

    def complete(self, job_id: int, worker_id: str) -> bool:
        return self._update_held(
            job_id, worker_id, status="succeeded", finished_at=func.now(),
            locked_by=None, locked_until=None, last_error=None,
        )  # fmt: skip

    def fail(self, job_id: int, worker_id: str, *, error: str, retryable: bool) -> bool:
        """Record a failure: requeue with backoff if retryable and attempts remain,
        otherwise fail the job for good."""
        job = self._session.get_one(Job, job_id)
        self._session.refresh(job)
        if job.status != "running" or job.locked_by != worker_id:
            return False
        if retryable and job.attempts < job.max_attempts:
            return self._update_held(
                job_id, worker_id, status="queued", last_error=error,
                run_after=func.now() + retry_delay(job.attempts),
                locked_by=None, locked_until=None,
            )  # fmt: skip
        return self._update_held(
            job_id, worker_id, status="failed", last_error=error, finished_at=func.now(),
            locked_by=None, locked_until=None,
        )  # fmt: skip

    # --------------------------------------------------------------- recovery ---

    def recover_expired_leases(self) -> int:
        """Requeue (or fail, if out of attempts) running jobs whose lease has expired:
        their worker crashed or lost its connection. Returns how many were recovered."""
        expired = and_(Job.status == "running", Job.locked_until < func.now())
        requeued = self._session.execute(
            update(Job)
            .where(expired, Job.attempts < Job.max_attempts)
            .values(
                status="queued",
                run_after=func.now(),
                locked_by=None,
                locked_until=None,
                last_error="lease expired (worker lost)",
                updated_at=func.now(),
            )  # fmt: skip
            .execution_options(synchronize_session=False)
        ).rowcount
        failed = self._session.execute(
            update(Job)
            .where(expired, Job.attempts >= Job.max_attempts)
            .values(
                status="failed",
                finished_at=func.now(),
                locked_by=None,
                locked_until=None,
                last_error="lease expired (worker lost), no attempts left",
                updated_at=func.now(),
            )  # fmt: skip
            .execution_options(synchronize_session=False)
        ).rowcount
        return requeued + failed

    # ---------------------------------------------------------------- helpers ---

    def _update_held(self, job_id: int, worker_id: str, **values: Any) -> bool:
        statement = (
            update(Job)
            .where(Job.id == job_id, Job.status == "running", Job.locked_by == worker_id)
            .values(**values, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return self._session.execute(statement).rowcount == 1
