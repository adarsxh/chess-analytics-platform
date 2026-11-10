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

- [Architecture](docs/ARCHITECTURE.md): components, boundaries, data flow
- [Decisions](docs/DECISIONS.md): architecture decision records (ADRs)
- [Data model](docs/DATA_MODEL.md): schema principles and tables

## Roadmap

| Version | Focus |
|---|---|
| V1 | Chess.com ingestion, Stockfish analysis, basic dashboard |
| V2 | Better analytics: openings, weaknesses, time controls, accuracy trends |
| V3 | Multiple platforms through additional ingestion adapters |
| V4 | User accounts, auth, user-specific analytics, background processing |
| V5 | Cloud deployment, managed DB, scalable workers, monitoring |
| V6 | Airflow orchestration, data quality, Parquet/Delta analytics |

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
docs/                  architecture, decisions, data model
```
