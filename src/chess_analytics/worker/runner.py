"""The worker loop: claim a job, run its handler, record the outcome. Repeat.

- Busy: after a job finishes, the next one is claimed immediately.
- Idle: waits `poll_interval` between checks (decision Q2-a).
- One job at a time (decision Q4-a); scale by running more worker processes.
- While a job runs, a background thread renews its lease every lease / 3, so a long
  job is never mistaken for a crashed one.
- Every `recover_interval`, leases of crashed workers are recovered.
- Stopping (`stop` event) never interrupts a job: the current one finishes first.
"""

import threading
import time
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.storage.repositories.jobs import JobRepository
from chess_analytics.worker.handlers import Handler, PermanentJobError

log = structlog.get_logger(__name__)


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: Mapping[str, Handler],
        *,
        worker_id: str,
        poll_interval: float = 2.0,
        lease: timedelta = timedelta(seconds=60),
        recover_interval: float = 30.0,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = handlers
        self.worker_id = worker_id
        self._poll_interval = poll_interval
        self._lease = lease
        self._recover_interval = recover_interval
        self._last_recovery = float("-inf")

    def run(self, stop: threading.Event) -> None:
        log.info("worker_started", worker_id=self.worker_id)
        while not stop.is_set():
            self._recover_if_due()
            if not self.run_once():
                stop.wait(self._poll_interval)  # idle: sleep, but wake at once on stop
        log.info("worker_stopped", worker_id=self.worker_id)

    def run_once(self) -> bool:
        """Claim and run one job. Returns False if there was nothing to do."""
        with self._session_factory.begin() as session:
            job = JobRepository(session).claim(self.worker_id, lease=self._lease)
            if job is None:
                return False
            job_id, job_type = job.id, job.job_type
            payload: dict[str, Any] = dict(job.payload)
            attempt, max_attempts = job.attempts, job.max_attempts

        # Every log line emitted while this job runs carries its id and type.
        with structlog.contextvars.bound_contextvars(job_id=job_id, job_type=job_type):
            log.info("job_started", attempt=attempt, max_attempts=max_attempts)
            started = time.monotonic()
            error, retryable = self._execute(job_id, job_type, payload)
            self._finish(job_id, error=error, retryable=retryable)
            log.info(
                "job_finished",
                outcome="succeeded" if error is None else "failed",
                retryable=retryable if error else None,
                seconds=round(time.monotonic() - started, 2),
            )
        return True

    # ---------------------------------------------------------------- helpers ---

    def _execute(
        self, job_id: int, job_type: str, payload: dict[str, Any]
    ) -> tuple[str | None, bool]:
        """Run the handler with heartbeats. Returns (error, retryable); error None = ok."""
        handler = self._handlers.get(job_type)
        if handler is None:
            return f"PermanentJobError: no handler for job type {job_type!r}", False

        done = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat, args=(job_id, done), name=f"heartbeat-{job_id}", daemon=True
        )
        heartbeat.start()
        try:
            handler(payload)
            return None, False
        except PermanentJobError as exc:
            return f"PermanentJobError: {exc}", False
        except Exception as exc:  # RetryableJobError, and anything unexpected
            log.exception("job_error")
            return f"{type(exc).__name__}: {exc}", True
        finally:
            done.set()
            heartbeat.join()

    def _heartbeat(self, job_id: int, done: threading.Event) -> None:
        interval = self._lease.total_seconds() / 3
        while not done.wait(interval):
            try:
                with self._session_factory.begin() as session:
                    held = JobRepository(session).heartbeat(
                        job_id, self.worker_id, lease=self._lease
                    )
            except Exception:  # a missed heartbeat is not fatal; the next one may succeed
                log.exception("heartbeat_error", job_id=job_id)
                continue
            if not held:
                log.warning("lease_lost", job_id=job_id)
                return

    def _finish(self, job_id: int, *, error: str | None, retryable: bool) -> None:
        with self._session_factory.begin() as session:
            jobs = JobRepository(session)
            recorded = (
                jobs.complete(job_id, self.worker_id)
                if error is None
                else jobs.fail(job_id, self.worker_id, error=error, retryable=retryable)
            )
        if not recorded:
            # Our lease expired and the job was recovered: its new owner decides the outcome.
            log.warning("job_outcome_not_recorded", reason="lease no longer held")

    def _recover_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_recovery < self._recover_interval:
            return
        self._last_recovery = now
        with self._session_factory.begin() as session:
            recovered = JobRepository(session).recover_expired_leases()
        if recovered:
            log.warning("expired_leases_recovered", count=recovered)
