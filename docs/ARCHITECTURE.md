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
| `classification` | Pure functions: evaluation sequence → inaccuracy / mistake / blunder | engine internals, storage |
| `analytics` | Player and game metrics (W/L/D, rating series, openings, error rates) | api, frontend |
| `storage` | Table definitions, sessions, repositories, idempotent upserts | api, business rules |
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

1. Frontend calls `POST /players/{platform}/{username}/sync` with a month range.
2. API validates the input and confirms the player exists on the platform, upserts
   the player, enqueues an **ingestion job**, and returns a `job_id` immediately.
3. Worker claims the job, lists the monthly archives, and fetches only months that
   are new or changed (conditional requests plus an archive fetch log).
4. **Raw layer:** each game is stored exactly as received, keyed by
   `(platform, platform_game_id)`. Re-fetching an unchanged game is a no-op.
5. **Processed layer:** new raw games are normalised into games and moves, with a
   reference to the raw record and the `parser_version`.
6. Analysis jobs are enqueued for the latest `AUTO_ANALYSE_LATEST_N` standard-chess
   games; the rest can be requested on demand.
7. **Analysis layer:** Stockfish evaluates each position. Evaluations and
   classifications are stored under an analysis run that records the engine version
   and full configuration.
8. Analytics endpoints compute statistics with SQL at read time.
9. Frontend polls job status and renders the dashboard.

Each of steps 3–4, 5 and 7 can be **re-run on its own**: re-parse without re-fetching,
or re-analyse with a new engine version without re-fetching or re-parsing.

## 5. Logical data layers

| Layer | Contents | Key properties |
|---|---|---|
| Raw | Source payloads exactly as received, with source IDs, retrieval timestamps and content hash | Append/upsert only; never transformed in place |
| Processed | Normalised games, players, moves, openings, results, ratings | Reproducible from raw; tagged with `parser_version` |
| Analysis | Engine evaluations and move classifications | Kept separate from processed data; tagged with engine and classifier versions |

These are logical layers inside one PostgreSQL database, not separate systems.
Table-level design: [DATA_MODEL.md](DATA_MODEL.md).

## 6. Cross-cutting concerns

- **Configuration:** environment variables loaded via `pydantic-settings`;
  `.env.example` documents every setting. No secrets in code.
- **Logging:** structured (JSON) logs with job and player identifiers for correlation.
- **Reliability:** HTTP retries with backoff (429 / 5xx); jobs record status, attempts
  and errors; ingestion failures and analysis failures are tracked separately.
- **Idempotency:** natural keys plus upserts at every layer; re-running a job is safe.

## 7. Explicitly out of scope for V1

Authentication and user accounts, multiple platforms, Airflow, Redis/Celery, cloud
deployment, Parquet/Delta exports. The boundaries above are designed so each can be
added later; see the roadmap (V2–V6) in the README.
