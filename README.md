# Chess Analytics Platform

[![CI](https://github.com/adarsxh/chess-analytics-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/adarsxh/chess-analytics-platform/actions/workflows/ci.yml)

Analyse your online chess games: ingest game history from Chess.com, run Stockfish
analysis, and explore results (win/loss/draw, rating progression, openings,
blunders and mistakes) through an API and a dashboard.

> **Status:** V1 in progress. Phase 1 (repository, environment and CI) is complete.

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
| V2 | Better analytics: openings, weaknesses, time controls, accuracy trends, results by opponent country |
| V3 | Multiple platforms through additional ingestion adapters |
| V4 | User accounts, auth, user-specific analytics, background processing |
| V5 | Cloud deployment, managed DB, scalable workers, monitoring |
| V6 | Airflow orchestration, data quality, Parquet/Delta analytics |

## Development setup

Prerequisites: [uv](https://docs.astral.sh/uv/), Python 3.13 (uv can install it), Docker,
and Stockfish for engine analysis (`brew install stockfish`; the Docker image ships the
pinned version from `scripts/install-stockfish.sh`).

```bash
uv sync                          # create .venv and install the project + dev tools
cp .env.example .env             # local config; adjust passwords if you like
docker compose up -d db          # start Postgres 18 in Docker
uv run alembic upgrade head      # create/upgrade the database schema
uv run python -m chess_analytics.api      # API at http://127.0.0.1:8000 (docs: /docs)
```

Check it: `curl localhost:8000/health` (liveness) and `curl localhost:8000/health/ready`
(readiness, which also checks the database).

To run the full stack in containers (the same image CI builds):

```bash
docker compose up --build        # db → migrate (one-off) → api; Ctrl+C to stop
docker compose down              # stop containers (add -v to also delete DB data)
```

## Syncing games from Chess.com

```bash
uv run python -m chess_analytics sync <username>                  # incremental
uv run python -m chess_analytics sync <username> --full-refresh   # ignore stored ETags
```

Each monthly archive is fetched with its stored ETag, so unchanged months cost one cheap
`304 Not Modified` request. Games are stored as received in `raw.raw_games`; re-running
is always safe. Every sync is recorded in `raw.ingestion_runs`.

After fetching, `sync` processes new games into `core.games`, `core.game_players` and
`core.game_moves`. To (re)process on its own, e.g. after bumping `PARSER_VERSION`:

```bash
uv run python -m chess_analytics process            # everything that needs processing
uv run python -m chess_analytics process --limit 50
```

Games that are not processed (variants such as odds chess, or inconsistent data) are
listed with the reason in `core.processing_failures`.

## Background worker

Long-running work (syncs now, engine analysis later) runs in a separate worker process
that takes jobs from a Postgres queue (`ops.jobs`).

```bash
uv run python -m chess_analytics.worker               # run a worker (Ctrl+C finishes the current job)
uv run python -m chess_analytics request-sync <user>  # queue a sync for it
```

In Docker, `docker compose up` starts a worker next to the API; add workers with
`docker compose up --scale worker=3`. Duplicate requests for the same player return the
job already queued; failed jobs are retried with backoff (30 s, 2 min, 8 min).

## API

Interactive docs: http://127.0.0.1:8000/docs (with the API running). Business endpoints are
versioned under `/api/v1`; `/health` and `/health/ready` are not.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/players/{username}/sync` | Queue a sync → `202` + job (duplicates return the same job) |
| GET | `/api/v1/jobs/{job_id}` | Job status: queued / running / succeeded / failed |
| GET | `/api/v1/players/{username}` | Profile and win/loss/draw record |
| GET | `/api/v1/players/{username}/games` | Games, newest first: `limit`, `offset`, `time_class`, `outcome`, `rated` |
| GET | `/api/v1/games/{game_id}` | One game with both sides and moves (`include_moves=false` to skip) |

## Database migrations

The schema is managed by [Alembic](https://alembic.sqlalchemy.org/). Migrations run as an
explicit step, never at app startup (ADR-020).

```bash
uv run alembic upgrade head      # apply pending migrations (run after every git pull)
uv run alembic current           # which revision is the database on?
uv run alembic history           # list all migrations
uv run alembic check             # fails if models changed without a migration
uv run alembic revision --autogenerate --rev-id 0002 -m "describe change"
```

Always read an autogenerated migration before committing it: autogenerate is a draft,
not a guarantee.

## Running tests

```bash
uv run pytest                    # unit + engine tests; integration tests are skipped
CHESS_TEST_DATABASE_URL=postgresql+psycopg://chess:change-me@localhost:5432/chess_analytics \
  uv run pytest -m integration   # integration tests against the compose Postgres
uv run pytest -m live            # opt-in: smoke test against the real Chess.com API
uv run ruff check . && uv run ruff format --check .
```

Live tests are excluded by default and never run in CI; all other tests use mocked HTTP
with synthetic data. Engine tests (`tests/engine`) run a real Stockfish found on `PATH`
(or `CHESS_TEST_ENGINE_PATH`) and are skipped locally if none is installed; CI installs
the pinned version and requires them.

End-to-end tests run the whole stack in Docker, with Chess.com replaced by a local stub
(CI runs them on every PR):

```bash
docker compose -f compose.yaml -f tests/e2e/compose.yaml --env-file .env.example up -d --build --wait
uv run pytest -m e2e
docker compose -f compose.yaml -f tests/e2e/compose.yaml --env-file .env.example down -v
```

## Contributing workflow

`main` is protected: every change goes through a short-lived branch and a PR, and CI
(`lint`, `test`, `docker`) must pass before squash-merging. See ADR-013.

```bash
git checkout -b feat/<topic>
# ...commit...
git push -u origin feat/<topic>
gh pr create --fill
gh pr checks --watch             # wait for CI
gh pr merge --squash --delete-branch
```

## Project layout

```
src/chess_analytics/   application package (one subpackage per component)
tests/                 unit and integration tests
compose.yaml           local stack: Postgres + API
Dockerfile             multi-stage API image
alembic.ini            Alembic config (DB URL comes from settings)
migrations/            schema migrations (versions/ = history)
src/…/storage/models/  table definitions, one module per Postgres schema
.github/workflows/     CI pipeline
.github/rulesets/      branch protection for main (as code)
docs/                  architecture, decisions, data model
```
