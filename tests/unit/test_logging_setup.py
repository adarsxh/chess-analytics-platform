import json
import logging

import pytest
import structlog

from chess_analytics.logging_setup import configure_logging


def last_json_line(capsys: pytest.CaptureFixture[str]) -> dict:
    lines = capsys.readouterr().out.strip().splitlines()
    return json.loads(lines[-1])


def test_structlog_event_rendered_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", fmt="json")
    structlog.get_logger("test").info("archive_fetched", month="2026-10", games=118)

    event = last_json_line(capsys)
    assert event["event"] == "archive_fetched"
    assert event["games"] == 118
    assert event["level"] == "info"
    assert "timestamp" in event


def test_bound_context_is_included(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", fmt="json")
    structlog.contextvars.bind_contextvars(job_id=42)
    try:
        structlog.get_logger("test").info("job_started")
    finally:
        structlog.contextvars.clear_contextvars()

    assert last_json_line(capsys)["job_id"] == 42


def test_stdlib_logs_use_same_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", fmt="json")
    logging.getLogger("some.third_party").warning("retrying request")

    event = last_json_line(capsys)
    assert event["event"] == "retrying request"
    assert event["logger"] == "some.third_party"
    assert event["level"] == "warning"


def test_level_filters_lower_events(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="WARNING", fmt="json")
    structlog.get_logger("test").info("should_not_appear")
    assert capsys.readouterr().out == ""
