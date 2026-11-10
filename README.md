# Chess Analytics Platform

Analyse your online chess games: ingest game history from Chess.com, run Stockfish
analysis, and explore results (win/loss/draw, rating progression, openings,
blunders and mistakes) through an API and a dashboard.

> **Status:** V1 in progress, Phase 1 (repository and environment setup).

## Architecture at a glance

A modular monolith in Python: one codebase with clear internal boundaries and two
runtime entrypoints, an **API** (FastAPI) and a **worker** (ingestion and engine
analysis). PostgreSQL is used for storage and Streamlit for the frontend, which
talks to the backend only over HTTP.

See `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` (added in Phase 1, Step 2).

## Development setup

Prerequisites: [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv can install it).

```bash
uv sync                 # create .venv and install the project + dev tools
uv run pytest           # run tests
uv run ruff check .     # lint
uv run ruff format .    # format
```

## Project layout

```
src/chess_analytics/   application package (one subpackage per component)
tests/                 unit and integration tests
```
