# Architecture

> Living document. Update it whenever a component's responsibility or a boundary changes.
> The reasoning behind each choice is in [DECISIONS.md](DECISIONS.md).

## 1. Overview

The platform ingests a player's games from an online chess platform (Chess.com first),
preserves the raw data, normalises it into a platform-neutral model, analyses it with
Stockfish, and serves statistics to a frontend through an HTTP API.

It is a **modular monolith**: one Python codebase (`src/chess_analytics/`) with strict
internal boundaries, deployed as a small number of processes:

| Process  | Entrypoint                    | Purpose                                    |
|----------|-------------------------------|--------------------------------------------|
| API      | `chess_analytics.api`         | HTTP requests: validation, job submission, reads |
| Worker   | `chess_analytics.worker`      | Long-running work: ingestion, parsing, engine analysis |
| Frontend | `frontend/` (Streamlit)       | UI; talks to the API over HTTP only        |
| Database | PostgreSQL                    | All persistent state, including the job queue |

## 2. Component diagram

```
  ┌──────────────────────┐
  │  Frontend (Streamlit)│   HTTP only — never imports backend code
  └──────────┬───────────┘
             │ REST / JSON
  ┌──────────▼───────────┐
  │  API (FastAPI)       │
  └───┬──────────────┬───┘
      │ enqueue job  │ query
      ▼              ▼
  ┌────────┐   ┌──────────────────┐
  │  jobs  │   │ analytics        │
  └───┬────┘   └────────┬─────────┘
      │ claim           │
  ┌───▼─────────────────┴──────────────────────────────┐
  │ Worker                                             │
  │  ingestion ──▶ parsing ──▶ engine ──▶ classification│
  └────────┬────────────────────────────────┬──────────┘
           ▼                                ▼
     Chess.com API                 Stockfish (UCI subprocess)
                    │
  ┌─────────────────▼──────────────────────────────────┐
  │ storage (PostgreSQL)                               │
  │   raw layer  │  processed layer  │  analysis layer │
  └────────────────────────────────────────────────────┘
```

## 3. Components

| Package | Responsibility | Must not depend on |
|---|---|---|
| `domain` | Platform-neutral dataclasses and enums (`Game`, `Move`, `GameResult`, `TimeClass`, …) | Anything else in the project |
| `ingestion` | `GameSource` contract: fetch player profiles, archive lists and raw games | storage, engine, api |
| `ingestion/chesscom` | Chess.com HTTP client (rate limits, retries) and the Chess.com → domain mapping | other adapters |
| `parsing` | Platform-neutral PGN → moves/positions (via `python-chess`) | any specific platform |
| `engine` | Stockfish wrapper; analysis configuration (engine version, node limit, threads) | ingestion, api |
| `classification` | Pure functions: evaluation sequence → move labels by win-probability loss (ADR-024) | engine internals, storage |
| `analytics` | Player and game metrics (W/L/D, rating series, openings, error rates) | api, frontend |
| `storage` | Table definitions, sessions, repositories, idempotent upserts | api, business rules |
| `services` | Use cases that orchestrate adapters and repositories (e.g. `sync_player`); the one place `ingestion` and `storage` meet | api, specific platforms |
| `cli` | `python -m chess_analytics`: thin entry point that wires settings, DB and adapters, then calls services | business logic |
| `jobs` | Postgres-backed queue (enqueue / claim / complete / fail) and job handlers | api |
| `api` | Thin HTTP layer: request/response schemas, routing | SQL details, engine details |
| `worker` | Loop that claims jobs and dispatches them to handlers | api |

### Dependency rule

Dependencies point **inward towards `domain`**. `domain` imports nothing from the project.
Platform-specific knowledge (for example, what Chess.com's `"timeout"` result means) lives
only in that platform's adapter. Adding a new platform means adding
`ingestion/<platform>/`; analytics, engine and storage code stays unchanged.

### Where the abstractions stop

- **Ingestion** is abstracted (`GameSource`), because we expect multiple platforms.
- **Operational storage** (players, games, jobs) is accessed through repositories.
- **Analytics queries** are deliberately SQL specific to the backend, kept in
  `analytics/`. Moving to DuckDB or Delta later means rewriting that module, not the
  business logic. See ADR-008.

## 4. End-to-end data flow (V1)

1. A client (the dashboard, `curl`) calls `POST /api/v1/players/{username}/sync`
   (`?platform=chesscom` by default; ADR-021).
2. API validates the username format, enqueues a deduplicated `sync_player` job in
   `ops.jobs` and returns `202` with the job immediately. A sync already queued or running
   for that player is returned instead of a duplicate.
3. Worker claims the job (`SKIP LOCKED`, lease plus heartbeats; ADR-022), looks up the
   player's profile (unknown player → job fails permanently, no retries), lists the
   monthly archives, and fetches only months that are new or changed (ETag conditional
   requests plus an archive fetch log).
4. **Raw layer:** each game is stored exactly as received, keyed by
   `(platform, platform_game_id)`. Re-fetching an unchanged game is a no-op.
5. **Processed layer:** in the same job, new raw versions are normalised into games,
   sides and moves (one transaction per game), with a reference to the raw record and the
   `parser_version`. Rejected games are recorded in `core.processing_failures`.
6. After a successful sync the worker enqueues `analyse_game` jobs (background priority)
   for the player's latest `CHESS_AUTO_ANALYSE_LATEST_N` (20) games not yet analysed;
   any other game is analysed on demand (`POST /api/v1/games/{id}/analysis`, ahead of
   background work). Only standard chess reaches `core`, so every game is analysable.
7. **Analysis layer:** Stockfish evaluates each position. Evaluations and
   classifications are stored under an analysis run that records the engine version
   and full configuration.
8. Analytics endpoints compute statistics with SQL at read time.
9. Client polls `GET /api/v1/jobs/{job_id}` and reads games through the API.

Steps 1–5 and 9 are implemented and tested end to end in CI (Phase 5); steps 6–8
arrive with Phase 6 (engine) and Phase 7 (analytics and dashboard).

Each of steps 3–4, 5 and 7 can be **re-run on its own**: re-parse without re-fetching,
or re-analyse with a new engine version without re-fetching or re-parsing.

## 5. Logical data layers

| Layer | Contents | Key properties |
|---|---|---|
| Raw | Source payloads exactly as received, with source IDs, retrieval timestamps and content hash | Append/upsert only; never transformed in place |
| Processed | Normalised games, players, moves, openings, results, ratings | Reproducible from raw; tagged with `parser_version` |
| Analysis | Engine evaluations and move classifications | Kept separate from processed data; tagged with engine and classifier versions |

These layers are separate Postgres **schemas** (`raw`, `core`, `analysis`, plus `ops` for
the job queue) in one database, not separate systems (ADR-019).
Table-level design: [DATA_MODEL.md](DATA_MODEL.md).

## 6. Cross-cutting concerns

- **Configuration:** environment variables loaded via `pydantic-settings`;
  `.env.example` documents every setting. No secrets in code.
- **Logging:** `structlog` (ADR-010), set up in `logging_setup.py`; JSON in containers,
  console locally; job and player identifiers bound as context for correlation.
- **Reliability:** HTTP retries with backoff (429 / 5xx); jobs record status, attempts
  and errors; ingestion failures and analysis failures are tracked separately.
- **Idempotency:** natural keys plus upserts at every layer; re-running a job is safe.
- **Health:** `GET /health` (liveness, no dependency checks) and `GET /health/ready`
  (readiness, checks Postgres, 503 when unavailable). See ADR-012.
- **Database access:** synchronous SQLAlchemy 2.0 + psycopg 3, one engine per process
  created in the app lifespan (ADR-011).
- **Schema migrations:** Alembic, run as a separate step before app processes start
  (`migrate` service in compose, a CI step, a release step later). The API and worker never
  change the schema (ADR-020).

## 7. Delivery pipeline

| Tool | Role in this project |
|---|---|
| **GitHub Actions** | Checks the *software* on every change: lint, tests (with Postgres), image build, and an end-to-end run of the whole stack against a Chess.com stub (ADR-013, ADR-022) |
| **Docker** | Packages the application and its dependencies into an image that runs the same everywhere |
| **Airflow** (later, V6) | Orchestrates the *data pipeline* on a schedule: ingestion, re-analysis, backfills |

CI does not deploy. Continuous delivery is added once a deployment target is chosen.

## 8. Explicitly out of scope for V1

Authentication and user accounts, multiple platforms, Airflow, Redis/Celery, cloud
deployment, Parquet/Delta exports. The boundaries above are designed so each can be
added later; see the roadmap (V2–V6) in the README.
