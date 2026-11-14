"""The worker loop and the sync handler, against the real schema."""

import threading
import time
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.ingestion.base import ArchiveRef, SourceGame, SourcePlayer
from chess_analytics.services.sync import request_sync
from chess_analytics.storage.models.ops import Job
from chess_analytics.storage.models.raw import RawGame
from chess_analytics.storage.repositories.jobs import JobRepository
from chess_analytics.worker.handlers import (
    PermanentJobError,
    RetryableJobError,
    handle_sync_player,
)
from chess_analytics.worker.runner import Worker
from tests.fakes import FakeGameSource
from tests.samples import chesscom_game

pytestmark = pytest.mark.integration

# Long enough that no heartbeat fires: a heartbeat thread must not share the test's
# single connection. Heartbeats get their own test with real connections below.
NO_HEARTBEAT = timedelta(hours=1)


def make_worker(session_factory, handlers, **kwargs) -> Worker:
    return Worker(session_factory, handlers, worker_id="test-worker", lease=NO_HEARTBEAT,
                  poll_interval=0.01, **kwargs)  # fmt: skip


def job_state(session_factory, job_id: int) -> Job:
    with session_factory() as session:
        return session.get_one(Job, job_id)


def enqueue(session_factory, job_type="sync_player", payload=None) -> int:
    with session_factory.begin() as session:
        return JobRepository(session).enqueue(job_type, payload or {"n": 1})[0].id


# ------------------------------------------------------------ worker loop ---


def test_nothing_to_do(session_factory) -> None:
    assert make_worker(session_factory, {}).run_once() is False


def test_successful_job_is_completed(session_factory) -> None:
    seen = []
    job_id = enqueue(session_factory, payload={"username": "someone"})

    assert make_worker(session_factory, {"sync_player": seen.append}).run_once() is True

    assert seen == [{"username": "someone"}]
    assert job_state(session_factory, job_id).status == "succeeded"


def test_permanent_error_fails_the_job(session_factory) -> None:
    def handler(payload):
        raise PermanentJobError("player not found: ghost")

    job_id = enqueue(session_factory)
    make_worker(session_factory, {"sync_player": handler}).run_once()

    job = job_state(session_factory, job_id)
    assert (job.status, job.last_error) == ("failed", "PermanentJobError: player not found: ghost")


@pytest.mark.parametrize("error", [RetryableJobError("503"), RuntimeError("unexpected bug")])
def test_retryable_and_unexpected_errors_are_retried(session_factory, error) -> None:
    def handler(payload):
        raise error

    job_id = enqueue(session_factory)
    make_worker(session_factory, {"sync_player": handler}).run_once()

    job = job_state(session_factory, job_id)
    assert (job.status, job.attempts) == ("queued", 1)
    assert type(error).__name__ in job.last_error


def test_unknown_job_type_fails_permanently(session_factory) -> None:
    job_id = enqueue(session_factory, job_type="analyse_game")
    make_worker(session_factory, {}).run_once()
    assert job_state(session_factory, job_id).status == "failed"


def test_run_processes_jobs_until_stopped(session_factory) -> None:
    stop, done = threading.Event(), []
    ids = [enqueue(session_factory, payload={"n": n}) for n in range(3)]

    def handler(payload):
        done.append(payload["n"])
        if len(done) == 3:
            stop.set()  # stop requested mid-job: the job still completes

    make_worker(session_factory, {"sync_player": handler}).run(stop)

    assert done == [0, 1, 2]
    assert {job_state(session_factory, i).status for i in ids} == {"succeeded"}


# ------------------------------------------------------------- heartbeats ---


@pytest.fixture
def real_session_factory(migrated_engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Real commits on separate connections (needed for a second thread). Cleans up."""
    factory = sessionmaker(migrated_engine)
    yield factory
    with migrated_engine.begin() as connection:
        connection.execute(delete(Job))


def test_long_jobs_keep_their_lease_alive(real_session_factory, monkeypatch) -> None:
    beats = []
    original = JobRepository.heartbeat

    def counting_heartbeat(self, job_id, worker_id, *, lease):
        beats.append(job_id)
        return original(self, job_id, worker_id, lease=lease)

    monkeypatch.setattr(JobRepository, "heartbeat", counting_heartbeat)
    job_id = enqueue(real_session_factory)
    slow_handler = {"sync_player": lambda payload: time.sleep(1.0)}
    # A 0.9 s lease means a heartbeat every 0.3 s while the 1 s job runs.
    worker = Worker(real_session_factory, slow_handler, worker_id="w", lease=timedelta(seconds=0.9))

    worker.run_once()

    assert len(beats) >= 2
    assert job_state(real_session_factory, job_id).status == "succeeded"


# ----------------------------------------------------------- sync handler ---


OCT = ArchiveRef(month=date(2026, 10, 1), url="https://example.test/2026/10")


def fake_source(**overrides) -> FakeGameSource:
    values = {
        "players": {"someone": SourcePlayer("chesscom", "SomeOne", platform_player_id=7)},
        "archives": {"someone": [OCT]},
        "games": {OCT.url: (SourceGame("g-1", chesscom_game(uuid="g-1")),)},
    }
    return FakeGameSource(**(values | overrides))


def run_handler(session_factory, source, payload) -> None:
    handle_sync_player(payload, session_factory=session_factory,
                       source_factory=lambda platform: nullcontext(source))  # fmt: skip


def test_sync_handler_syncs_the_player(session_factory) -> None:
    run_handler(session_factory, fake_source(), {"platform": "chesscom", "username": "someone"})
    with session_factory() as session:
        assert session.scalar(select(RawGame.platform_game_id)) == "g-1"


def test_sync_handler_unknown_player_is_permanent(session_factory) -> None:
    with pytest.raises(PermanentJobError, match="not found"):
        run_handler(session_factory, fake_source(), {"platform": "chesscom", "username": "ghost"})


def test_sync_handler_source_outage_is_retryable(session_factory) -> None:
    source = fake_source(failing_urls={OCT.url})
    with pytest.raises(RetryableJobError, match="SourceUnavailableError"):
        run_handler(session_factory, source, {"platform": "chesscom", "username": "someone"})


def test_sync_handler_rejects_a_malformed_payload(session_factory) -> None:
    with pytest.raises(PermanentJobError, match="invalid"):
        run_handler(session_factory, fake_source(), {"username": "someone"})


# ----------------------------------------------------------- request_sync ---


def test_request_sync_deduplicates_case_insensitively(session_factory) -> None:
    first, created = request_sync(session_factory, platform="chesscom", username="SomeOne")
    again, created_again = request_sync(session_factory, platform="chesscom", username="someone")
    assert created and not created_again
    assert again == first
