"""The Postgres job queue against the real schema.

Note: inside one transaction Postgres' now() is constant, so "time passing" (backoff
elapsed, lease expired) is simulated by moving run_after / locked_until into the past.
"""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, delete, text, update
from sqlalchemy.orm import Session

from chess_analytics.storage.models.ops import Job
from chess_analytics.storage.repositories.jobs import JobRepository, retry_delay

pytestmark = pytest.mark.integration

LEASE = timedelta(seconds=60)
SYNC = {"platform": "chesscom", "username": "someone"}


@pytest.fixture
def jobs(session: Session) -> JobRepository:
    return JobRepository(session)


def reload(session: Session, job_id: int) -> Job:
    session.expire_all()  # read what the database holds now, not cached ORM state
    return session.get_one(Job, job_id)


# ---------------------------------------------------------------- enqueue ---


def test_enqueue_creates_a_queued_job(jobs: JobRepository) -> None:
    job, created = jobs.enqueue("sync_player", SYNC, dedup_key="sync:chesscom:someone")
    assert created
    assert (job.status, job.attempts, job.max_attempts, job.payload) == ("queued", 0, 3, SYNC)


def test_enqueue_returns_the_active_job_instead_of_a_duplicate(jobs: JobRepository) -> None:
    first, _ = jobs.enqueue("sync_player", SYNC, dedup_key="sync:chesscom:someone")
    again, created = jobs.enqueue("sync_player", SYNC, dedup_key="sync:chesscom:someone")
    assert not created
    assert again.id == first.id


def test_a_duplicate_request_raises_the_priority_of_a_queued_job(
    session: Session, jobs: JobRepository
) -> None:
    background, _ = jobs.enqueue("analyse_game", {"game_id": 1}, dedup_key="a:1", priority=200)
    jobs.enqueue("analyse_game", {"game_id": 1}, dedup_key="a:1", priority=100)
    assert reload(session, background.id).priority == 100
    jobs.enqueue("analyse_game", {"game_id": 1}, dedup_key="a:1", priority=200)
    assert reload(session, background.id).priority == 100  # never lowered


def test_a_running_job_keeps_its_priority(session: Session, jobs: JobRepository) -> None:
    job, _ = jobs.enqueue("analyse_game", {"game_id": 1}, dedup_key="a:1", priority=200)
    jobs.claim("w1", lease=LEASE)
    again, created = jobs.enqueue("analyse_game", {"game_id": 1}, dedup_key="a:1", priority=100)
    assert (again.id, created, reload(session, job.id).priority) == (job.id, False, 200)


def test_finished_job_does_not_block_new_work(session: Session, jobs: JobRepository) -> None:
    first, _ = jobs.enqueue("sync_player", SYNC, dedup_key="k")
    claimed = jobs.claim("w1", lease=LEASE)
    jobs.complete(claimed.id, "w1")

    second, created = jobs.enqueue("sync_player", SYNC, dedup_key="k")
    assert created and second.id != first.id


def test_jobs_without_dedup_key_are_never_merged(jobs: JobRepository) -> None:
    a, _ = jobs.enqueue("analyse_game", {"game_id": 1})
    b, _ = jobs.enqueue("analyse_game", {"game_id": 1})
    assert a.id != b.id


# ------------------------------------------------------------------ claim ---


def test_claim_takes_highest_priority_then_oldest(jobs: JobRepository) -> None:
    low, _ = jobs.enqueue("analyse_game", {"n": 1}, priority=200)
    first, _ = jobs.enqueue("sync_player", {"n": 2}, priority=100)
    second, _ = jobs.enqueue("sync_player", {"n": 3}, priority=100)

    order = [jobs.claim("w1", lease=LEASE).id for _ in range(3)]
    assert order == [first.id, second.id, low.id]
    assert jobs.claim("w1", lease=LEASE) is None  # queue empty


def test_claim_marks_the_job_running_with_a_lease(session: Session, jobs: JobRepository) -> None:
    jobs.enqueue("sync_player", SYNC)
    claimed = jobs.claim("worker-a", lease=LEASE)

    job = reload(session, claimed.id)
    assert (job.status, job.attempts, job.locked_by) == ("running", 1, "worker-a")
    assert job.locked_until is not None and job.started_at is not None


def test_claim_skips_jobs_waiting_for_backoff(session: Session, jobs: JobRepository) -> None:
    job, _ = jobs.enqueue("sync_player", SYNC)
    session.execute(update(Job).where(Job.id == job.id).values(run_after=Job.created_at + LEASE))
    assert jobs.claim("w1", lease=LEASE) is None


# ---------------------------------------------------- complete / heartbeat ---


def test_complete_releases_the_lease(session: Session, jobs: JobRepository) -> None:
    jobs.enqueue("sync_player", SYNC)
    claimed = jobs.claim("w1", lease=LEASE)

    assert jobs.complete(claimed.id, "w1")
    job = reload(session, claimed.id)
    assert (job.status, job.locked_by, job.locked_until) == ("succeeded", None, None)
    assert job.finished_at is not None


def test_only_the_lease_holder_can_touch_a_running_job(
    session: Session, jobs: JobRepository
) -> None:
    jobs.enqueue("sync_player", SYNC)
    claimed = jobs.claim("w1", lease=LEASE)

    assert not jobs.heartbeat(claimed.id, "w2", lease=LEASE)
    assert not jobs.complete(claimed.id, "w2")
    assert not jobs.fail(claimed.id, "w2", error="x", retryable=False)
    assert reload(session, claimed.id).status == "running"
    assert jobs.heartbeat(claimed.id, "w1", lease=LEASE)


# ------------------------------------------------------------------- fail ---


def test_retryable_failure_requeues_with_backoff(session: Session, jobs: JobRepository) -> None:
    jobs.enqueue("sync_player", SYNC)
    claimed = jobs.claim("w1", lease=LEASE)

    jobs.fail(claimed.id, "w1", error="SourceUnavailableError: 503", retryable=True)

    job = reload(session, claimed.id)
    assert (job.status, job.attempts, job.locked_by) == ("queued", 1, None)
    assert job.last_error == "SourceUnavailableError: 503"
    assert job.run_after > job.created_at  # not claimable until the backoff has passed
    assert jobs.claim("w1", lease=LEASE) is None


def test_retries_stop_after_max_attempts(session: Session, jobs: JobRepository) -> None:
    job, _ = jobs.enqueue("sync_player", SYNC, max_attempts=2)
    for _ in range(2):
        session.execute(update(Job).where(Job.id == job.id).values(run_after=Job.created_at))
        claimed = jobs.claim("w1", lease=LEASE)
        jobs.fail(claimed.id, "w1", error="503", retryable=True)

    final = reload(session, job.id)
    assert (final.status, final.attempts) == ("failed", 2)
    assert final.finished_at is not None


def test_permanent_failure_is_not_retried(session: Session, jobs: JobRepository) -> None:
    jobs.enqueue("sync_player", SYNC)
    claimed = jobs.claim("w1", lease=LEASE)
    jobs.fail(claimed.id, "w1", error="PlayerNotFoundError: ghost", retryable=False)
    assert reload(session, claimed.id).status == "failed"


def test_backoff_grows_by_four_each_attempt() -> None:
    assert [retry_delay(n) for n in (1, 2, 3)] == [
        timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=8),
    ]  # fmt: skip


# --------------------------------------------------------------- recovery ---


def expire_lease(session: Session, job_id: int) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(locked_until=Job.created_at - LEASE))


def test_expired_lease_is_requeued(session: Session, jobs: JobRepository) -> None:
    jobs.enqueue("sync_player", SYNC)
    claimed = jobs.claim("crashed-worker", lease=LEASE)
    expire_lease(session, claimed.id)

    assert jobs.recover_expired_leases() == 1
    job = reload(session, claimed.id)
    assert (job.status, job.locked_by) == ("queued", None)
    assert "lease expired" in job.last_error
    assert jobs.claim("healthy-worker", lease=LEASE).id == claimed.id  # picked up again


def test_expired_lease_without_attempts_left_fails(session: Session, jobs: JobRepository) -> None:
    jobs.enqueue("sync_player", SYNC, max_attempts=1)
    claimed = jobs.claim("crashed-worker", lease=LEASE)
    expire_lease(session, claimed.id)

    jobs.recover_expired_leases()
    assert reload(session, claimed.id).status == "failed"


def test_live_leases_are_left_alone(jobs: JobRepository) -> None:
    jobs.enqueue("sync_player", SYNC)
    jobs.claim("w1", lease=LEASE)
    assert jobs.recover_expired_leases() == 0


def test_a_worker_that_lost_its_lease_cannot_complete(
    session: Session, jobs: JobRepository
) -> None:
    jobs.enqueue("sync_player", SYNC)
    claimed = jobs.claim("slow-worker", lease=LEASE)
    expire_lease(session, claimed.id)
    jobs.recover_expired_leases()
    jobs.claim("new-worker", lease=LEASE)

    assert not jobs.complete(claimed.id, "slow-worker")  # would clobber new-worker's run
    assert reload(session, claimed.id).locked_by == "new-worker"


# ------------------------------------------------------------ concurrency ---


@pytest.fixture
def committed_jobs(migrated_engine: Engine) -> Iterator[list[int]]:
    """Two jobs committed for real, so separate connections can see them."""
    with Session(migrated_engine) as session, session.begin():
        ids = [JobRepository(session).enqueue("sync_player", {"n": n})[0].id for n in (1, 2)]
    try:
        yield ids
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(delete(Job).where(Job.id.in_(ids)))


def test_concurrent_workers_never_claim_the_same_job(
    migrated_engine: Engine, committed_jobs: list[int]
) -> None:
    # Two workers, two connections, both mid-transaction at the same time.
    with Session(migrated_engine) as worker_a, Session(migrated_engine) as worker_b:
        # Without SKIP LOCKED, B would block on A's row lock: fail fast instead of hanging.
        worker_b.execute(text("SET LOCAL lock_timeout = '2s'"))
        job_a = JobRepository(worker_a).claim("a", lease=LEASE)  # row lock held until commit
        job_b = JobRepository(worker_b).claim("b", lease=LEASE)  # skips it, doesn't wait

        assert {job_a.id, job_b.id} == set(committed_jobs)
        assert JobRepository(worker_b).claim("b", lease=LEASE) is None  # nothing else left
        worker_a.rollback()
        worker_b.rollback()
