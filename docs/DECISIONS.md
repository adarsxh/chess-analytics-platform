# Architecture Decision Records

Each significant decision is recorded here and never silently edited. If a decision
changes, add a new ADR that **supersedes** the old one and mark the old one accordingly.

**Status values:** Proposed · Accepted · Superseded by ADR-XXX · Deprecated

**Template**

```
## ADR-XXX: Title
- Status / Date
### Context        what problem forced a decision
### Decision       what we chose
### Alternatives   what else we considered
### Consequences   trade-offs; what this enables and what it restricts
```

---

## ADR-001: Modular monolith

- **Status:** Accepted · **Date:** 2026-11-11

### Context
We need clear separation between ingestion, parsing, engine analysis, analytics, storage
and the API, and room to grow. The team is a single developer.

### Decision
One Python codebase with one package per component and an inward dependency rule
(see ARCHITECTURE.md). It runs as two backend processes, API and worker, built from
the same code.

### Alternatives
- **Microservices:** independent deployment and scaling, but network calls, distributed
  failures and operational overhead with no requirement justifying them.
- **Single script / notebook:** fastest to start, but no boundaries; every extension
  becomes a rewrite.

### Consequences
- (+) Simple to develop, test and deploy; refactoring across components is cheap.
- (+) Clean module boundaries mean a component could be extracted into a service later.
- (−) Boundaries are enforced by convention and review, not by the network. We may add
  an import-linting rule later.

---

## ADR-002: PostgreSQL as the primary datastore

- **Status:** Accepted · **Date:** 2026-11-11

### Context
The API and the worker write concurrently. We need raw payload storage, transactional
upserts for idempotency, and a database that will still fit multi-user V4/V5.

### Decision
PostgreSQL (Docker locally, managed later). Raw payloads stored as `JSONB`.
Schema managed with Alembic migrations.

### Alternatives
- **DuckDB:** excellent analytics engine, but allows only one writing process. The API
  and the worker would conflict over the file lock. Better suited as a future analytics
  or export layer.
- **SQLite:** simplest, but same weakness with concurrent writes; outgrown by V4.

### Consequences
- (+) Concurrency, transactions, JSONB, `SKIP LOCKED` queue (ADR-003), clear path to managed Postgres.
- (−) Docker required from day one; tests need a real Postgres (CI service container).
- Future: Parquet/DuckDB/Delta can be added as *derived* analytics stores without
  replacing Postgres as the system of record.

---

## ADR-003: Postgres-backed job queue with a separate worker

- **Status:** Accepted · **Date:** 2026-11-11

### Context
Ingesting archives takes seconds to minutes; analysing a game with Stockfish takes
roughly 10–60 s. This cannot happen inside an HTTP request.

### Decision
A `jobs` table in Postgres. Workers claim jobs with
`SELECT … FOR UPDATE SKIP LOCKED`, and record status, attempts and errors. A separate
worker process runs the job loop.

### Alternatives
- **FastAPI `BackgroundTasks`:** jobs lost on restart; work competes with request handling;
  cannot scale separately.
- **Celery + Redis:** mature and feature-rich, but adds a broker service and operational
  complexity before we need it.
- **CLI only:** simple, but no asynchronous UX from the frontend.

### Consequences
- (+) No extra infrastructure; jobs are durable and visible with plain SQL.
- (+) Horizontal scaling: run more worker processes; `SKIP LOCKED` prevents double-claiming.
- (−) We implement retries, timeouts and stale-job recovery ourselves.
- (−) Polling adds small latency and DB load; fine at our scale.
- Future: the `jobs` interface can be backed by Celery or a cloud queue if
  throughput requires it.

---

## ADR-004: Analysis scope and reproducible engine configuration

- **Status:** Accepted · **Date:** 2026-11-11

### Context
Full analysis is expensive (≈ 500 games × 80 positions × 0.3 s ≈ 3+ hours). Results must
be traceable and comparable across runs, and re-analysable with newer engines.

### Decision
- Automatically analyse the latest `AUTO_ANALYSE_LATEST_N` games (default **20**) after
  a sync; all other games are analysed **on demand**.
- Limit Stockfish by **node count** with **`Threads=1`**, not by wall-clock time.
- Every analysis run records engine name, engine version, node limit, threads, hash
  size, MultiPV and classifier version.

### Alternatives
- **Analyse everything in the background:** best coverage, but long queues and wasted
  compute on games nobody opens.
- **Time-limited search:** simpler to reason about latency, but results depend on
  hardware and load, so they aren't reproducible.
- **Multi-threaded search:** faster per position, but non-deterministic results.

### Consequences
- (+) Predictable cost; the dashboard has data right after the first sync.
- (+) Results are reproducible and comparable; re-analysis with a new engine creates a
  new run instead of overwriting the old one.
- (−) Aggregate error statistics initially cover only analysed games; the UI must state coverage.
- (−) Single-threaded analysis is slower per game; parallelism comes from multiple workers.

---

## ADR-005: Streamlit frontend, API-only communication

- **Status:** Accepted · **Date:** 2026-11-11

### Context
We need a working dashboard quickly, without coupling the UI to backend internals.

### Decision
Streamlit for V1. The frontend calls the FastAPI backend over HTTP and **never imports**
`chess_analytics` or touches the database.

### Alternatives
- **React / Next.js:** better UX and multi-user product potential; roughly doubles V1 effort.
- **Streamlit importing backend code directly:** faster, but recreates the coupling we want to avoid.

### Consequences
- (+) Fast iteration in Python.
- (+) The API is the only contract, so the frontend can be replaced later.
- (−) Limited control over layout, interactivity and auth flows; likely replaced by V4.

---

## ADR-006: Store all raw games; process only standard chess in V1

- **Status:** Accepted · **Date:** 2026-11-11

### Context
Chess.com archives include variants (chess960, bughouse, crazyhouse, …) and daily
(correspondence) games alongside live games.

### Decision
Persist **every** game in the raw layer. Parse and analyse only games with
`rules == "chess"` in V1. Keep time class (bullet / blitz / rapid / daily) as a dimension.

### Alternatives
- **Drop variants at ingestion:** smaller data, but loses source data and forces a re-fetch later.
- **Process all variants:** more coverage, but variant rules complicate parsing,
  engine analysis and statistics.

### Consequences
- (+) Supporting chess960 later means re-processing raw data, not re-fetching it.
- (−) Raw storage holds some rows that V1 never uses.

---

## ADR-007: Separate `players` from future `users`; games stored once

- **Status:** Accepted · **Date:** 2026-11-11

### Context
V1 has no accounts, but V3/V4 bring multiple platforms and multiple users. One game
involves two players, and both may be tracked.

### Decision
- A **player** is a chess identity on a platform: `(platform, platform_username)`.
- A **user** (V4) is an application account that may later link to several players.
- A **game** is a global record identified by `(platform, platform_game_id)`, linked to
  its white and black players. It is stored once, no matter whose sync fetched it.

### Alternatives
- **Games owned by the requesting user:** simpler queries, but duplicates shared games
  and mixes up "who asked" with "who played".

### Consequences
- (+) No duplicates; multi-platform and multi-user support need no schema rewrite.
- (+) A user can later link their Chess.com and Lichess identities.
- (−) Player-centric queries must filter on "white or black = player".

---

## ADR-008: Repositories for operational data; analytics SQL specific to the backend

- **Status:** Accepted · **Date:** 2026-11-11

### Context
We want storage independence, but a fully generic data access layer for analytics would
hide powerful SQL features and leak anyway.

### Decision
- CRUD and idempotent upserts for players, games, moves, jobs and analysis runs go through
  **repository classes** in `storage/`. SQLAlchemy 2.0 is used for table definitions and
  sessions.
- Aggregation queries live in `analytics/` and are knowingly **PostgreSQL SQL**.

### Alternatives
- **Repositories everywhere:** maximal abstraction, but an analytics layer that offers
  only the lowest common denominator.
- **Raw SQL everywhere:** maximal control, but business logic coupled to the database
  and harder to test.

### Consequences
- (+) Business logic is testable and independent of the database for operational data.
- (+) Analytics can use window functions, CTEs, etc. freely.
- (−) Moving analytics to DuckDB or Delta means rewriting `analytics/queries`; the scope
  of that is known and limited.

---

## ADR-009: `uv` for Python packaging and dependency management

- **Status:** Accepted · **Date:** 2026-11-11

### Context
We need reproducible environments across laptop, CI and Docker.

### Decision
`uv` with `pyproject.toml` as the single config file, a committed `uv.lock`, and a
`src/` layout package built with `uv_build`. Dev tools are kept in a `dev` dependency group.

### Alternatives
- **pip + requirements.txt:** universal, but no proper lockfile with transitive pinning.
- **Poetry:** mature, but slower and with its own config conventions.

### Consequences
- (+) Fast, deterministic installs; one tool manages Python versions, venvs and dependencies.
- (−) A newer tool; contributors need uv installed (single binary).

---

## ADR-010: structlog for structured logging

- **Status:** Accepted · **Date:** 2026-11-11

### Context
The API and the worker need machine-parseable logs that can be correlated by job and
player, plus readable output during local development.

### Decision
`structlog`, integrated with stdlib `logging` through `ProcessorFormatter`, so
third-party library logs (uvicorn, httpx, SQLAlchemy) share the same format. JSON by
default; console rendering opt-in via `CHESS_LOG_FORMAT=console`. Context such as
`job_id` is bound through `structlog.contextvars`.

### Alternatives
- **stdlib logging + custom JSON formatter:** no dependency, but more boilerplate, and
  context has to be passed manually via `extra={}` on every call.

### Consequences
- (+) Key-value events (`log.info("archive_fetched", games=118)`) are queryable in any log backend.
- (+) Context bound once at job start appears on every later line.
- (−) One more dependency; developers must call `configure_logging()` at each entrypoint.

---

## ADR-011: Synchronous database access with SQLAlchemy 2.0 + psycopg 3

- **Status:** Accepted · **Date:** 2026-11-12

### Context
Every repository, every route that touches the DB, and the worker depend on whether DB
access is sync or async. The worker is dominated by Stockfish CPU time; the API's
expected concurrency is modest.

### Decision
Synchronous SQLAlchemy 2.0 with the psycopg 3 driver (`postgresql+psycopg://`). Routes
that touch the DB are plain `def`; FastAPI runs them in its threadpool. One engine
(connection pool) per process, created in the app lifespan, with `pool_pre_ping` and a
connect timeout.

### Alternatives
- **Async (asyncpg + AsyncSession):** higher I/O concurrency, but async spreads through
  the whole codebase, lazy loading becomes an error, and testing and debugging are harder.
  Engine analysis gains nothing from it.
- **psycopg2:** mature but legacy; psycopg 3 is its maintained successor.

### Consequences
- (+) Simpler code, tests and stack traces.
- (+) psycopg 3 also supports async, so a later move to async is possible without changing driver.
- (−) Each in-flight DB request occupies a threadpool thread; fine at our scale, revisit
  if the API ever needs thousands of concurrent DB-bound requests.

---

## ADR-012: Local environment with Docker Compose, PostgreSQL 18, hybrid dev loop

- **Status:** Accepted · **Date:** 2026-11-12

### Context
We need a reproducible local Postgres and a container image that matches what CI and,
later, production will run, without slowing down daily development.

### Decision
- `compose.yaml` defines `db` (`postgres:18`, Debian-based) and `api` (our Dockerfile).
- **Hybrid loop:** day to day, run only `db` in Docker and the API natively with uv
  (fast reload, debugger). Use `docker compose up --build` to verify the full container.
- Multi-stage Dockerfile: a uv builder stage plus a slim runtime holding only the virtualenv,
  running as a non-root user. JSON logs and `0.0.0.0` binding are set in the image.
- Liveness (`/health`) never checks dependencies; readiness (`/health/ready`) checks the
  database and returns 503 when it is unreachable.
- `.env` is shared by the app (`CHESS_*`) and compose (`POSTGRES_*`), so app settings
  ignore unknown keys.

### Alternatives
- **Everything in Docker for dev:** closest to production, but slower feedback and harder debugging.
- **Locally installed Postgres:** no Docker, but version drift between machines and CI.
- **PostgreSQL 17:** more conservative; 18 is stable (18.6) with longer support.
- **Alpine Postgres image:** smaller, but musl/ICU collation differences from managed
  Postgres can change text sort order.

### Consequences
- (+) Same Postgres major version locally, in CI and (target) in production.
- (+) Readiness and liveness are separate, so orchestrators can hold back traffic
  without restarting the API.
- (−) The DB password appears twice in `.env` (in `POSTGRES_PASSWORD` and inside
  `CHESS_DATABASE_URL`), and the two must be kept in sync by hand.
- (−) Typos in `CHESS_*` keys in `.env` are silently ignored (trade-off of `extra="ignore"`).

---

## ADR-013: GitHub Actions CI with three parallel jobs; protected `main`

- **Status:** Accepted · **Date:** 2026-11-12

### Context
Nothing prevented broken code from reaching `main`. We need automated validation on
every change, and a rule that `main` only accepts validated changes. No deployment
target exists yet.

### Decision
- `.github/workflows/ci.yml` runs on every PR and every push to `main`, as three
  parallel jobs:
  - `lint`: `uv sync --locked`, ruff check, ruff format check
  - `test`: unit and integration tests against a `postgres:18` service container
  - `docker`: build the API image (not pushed) and run a smoke test
- Least-privilege token (`contents: read`), cancel out-of-date runs, job timeouts.
- In CI (`CI=true`), a missing test database URL **fails** instead of skipping.
- `main` is protected by a repository ruleset, versioned in `.github/rulesets/main.json`:
  - changes only through PRs, with 0 required approvals (solo developer)
  - squash merge only
  - `lint`, `test` and `docker` must pass, reported by the GitHub Actions app
  - the branch must be up to date with `main`
  - no force-push or deletion
  - no bypass, including for admins

### Alternatives
- **Single sequential job:** simpler, but slower, and an early failure hides later ones.
- **Admin bypass allowed:** handy for emergencies, but tends to turn into a habit.
- **No enforcement:** CI results would only be advisory.

### Consequences
- (+) `main` is always lint-clean, tested against real Postgres, and has a buildable image.
- (+) The protection rules are reviewed in code like everything else.
- (−) Every change, even a typo fix, needs a PR and a CI run of about 1–3 minutes.
- (−) Actions are pinned to major-version tags (setup-uv to an exact version, as it no
  longer publishes major tags), not commit SHAs. That is convenient, but
  a compromised tag would affect us; consider SHA pinning plus Dependabot later.
- Future: a deployment workflow (CD) is a separate, later decision once a target is chosen.

---

## ADR-014: Identity, keys and deduplication

- **Status:** Accepted · **Date:** 2026-11-13

### Context
Games appear in both players' archives and are re-fetched on every sync. Usernames can change.
Profiling real data showed games carry a game `uuid` and per-player `uuid`s, while the profile
endpoint exposes only a numeric `player_id`.

### Decision
- **Game identity:** `(platform, platform_game_id)` where the ID is the game's `uuid`. The URL is
  kept as an attribute.
- **Player identity:** `(platform, platform_uuid)`. The username is a mutable attribute, looked up
  case-insensitively. `platform_uuid` is nullable until the player's first game is seen.
  Opponents are stored as players.
- **Keys:** surrogate `bigint` identity PKs everywhere; natural keys enforced as `UNIQUE`.

### Alternatives
- Numeric game ID parsed from the URL: identity would depend on a URL format.
- Username as player identity: simple, but a rename splits one person into two players.
- Natural composite PKs: no extra column, but every child table repeats multi-column FKs.

### Consequences
- (+) No duplicate games; renames don't fragment history; compact FKs on large tables.
- (−) Linking a username lookup to a uuid needs a little logic, because the uuid is learned from games.

---

## ADR-015: Raw layer: versioned per-game payloads and ETag-based incremental fetch

- **Status:** Accepted · **Date:** 2026-11-13

### Context
Raw data must never be lost. Game payloads can change after first fetch (e.g. `accuracies`
appear once a game is reviewed). Real-data tests showed `If-None-Match` returns 304 while
`Last-Modified` is unreliable.

### Decision
- One `raw.raw_games` row per **distinct version** of a game object (JSONB), with
  `UNIQUE (platform, platform_game_id, content_hash)`. The hash is SHA-256 over canonical JSON.
  Re-seeing a version updates only `last_seen_at`; the current version = highest `last_seen_at`.
- `raw.archive_fetches` logs every archive request (status, ETag, counts). No payloads are stored there.
- Every sync revalidates **all** archives with ETags; a `full_refresh` mode ignores them.

### Alternatives
- Whole-archive snapshots: byte-exact, but a second raw table and an extraction step for no gain.
- Upsert-in-place or insert-once: lose history or miss late changes.
- Fetching only recent months: fewer requests, but misses late changes to old games.

### Consequences
- (+) Complete, append-only history with cheap deduplication; precise lineage per version.
- (+) Unchanged months cost one 304 request.
- (−) `last_seen_at` is the one mutable column on raw rows (metadata, never the payload).

---

## ADR-016: Processed (`core`) layer shape

- **Status:** Accepted · **Date:** 2026-11-13

### Context
Analytics are mostly player-centric ("my results, my ratings"). The engine needs positions per
move. Opening names differ across platforms.

### Decision
- `core` is **current state, rebuildable from `raw`**. It is not versioned. Each game records
  `raw_game_id` and `parser_version` for lineage. Reprocessing replaces a game's rows in one transaction.
- `core.games` holds game-level facts. `core.game_players` holds **two rows per game** (color,
  rating_after, result_code, normalised outcome, source accuracy). This resolves the
  "white OR black" query cost noted in ADR-007.
- `core.game_moves` has one row per ply, including `fen_after`, `clock_ms` and `time_spent_ms`.
- Openings: platform-provided `source_eco` / `source_opening_name` in V1. Our own cross-platform
  classification will be a later derived step.

### Alternatives
- Wide `games` with white_/black_ columns: every player query needs `OR` and `CASE`.
- Moves without FEN (replay on demand) or as an array column: less storage, harder analysis.
- Versioning `core`: triples complexity for data that can always be rebuilt.
- Own opening classification now: consistent across platforms, but work that is needed only in V3.

### Consequences
- (+) Simple, indexable player queries; per-move SQL analytics; direct engine input.
- (−) Showing one game needs a join; about 10 MB of FEN for ~156k moves.
- (−) Cross-platform opening statistics need future work.

---

## ADR-017: Analysis layer versioning

- **Status:** Accepted · **Date:** 2026-11-13

### Context
Engine results are expensive and depend on engine version and settings. Classification rules
will change far more often than engine runs.

### Decision
- `analysis_configs` (deduplicated by `config_hash`) → `game_analyses` with
  `UNIQUE (game_id, analysis_config_id)` → `position_evaluations` (one per position, White's
  point of view, `score_cp` XOR signed `mate_in`, `pv_rank` column).
- MultiPV = 1 in V1. MultiPV ≥ 2 ("only move" detection) is planned for V2 as a new config, with no migration needed.
- `move_classifications` keyed by `(game_analysis_id, ply, classifier_version)`.
- `game_analyses.moves_hash` vs `games.moves_hash` detects stale analyses.
- Cross-layer FKs (`game_analyses` → `games`) are `RESTRICT`.

### Alternatives
- Batch-level analysis runs: duplicates the job queue's role.
- MultiPV 2 now: about 1.5–2× engine cost for a feature outside V1.
- Classification stored on evaluations, or computed at query time: rule changes would overwrite
  history, or require Python per request.

### Consequences
- (+) Re-analysis adds data rather than replacing it; reclassification without the engine; reproducibility.
- (−) More tables and joins than a single "analysis" table.

---

## ADR-018: Jobs separate from ingestion runs; deduplication and leases

- **Status:** Accepted · **Date:** 2026-11-13

### Context
ADR-003 chose a Postgres queue and kept open the option to replace it. Users may trigger the
same work twice, and workers may crash mid-job.

### Decision
- `ops.jobs` is generic, replaceable infrastructure. `raw.ingestion_runs` and
  `analysis.game_analyses` are the domain records, and lineage points to them, never to jobs.
- `dedup_key` with a partial `UNIQUE` where status is `queued` or `running`. Enqueueing a
  duplicate returns the active job.
- Leases: `locked_until`, extended by a heartbeat; expired leases are reclaimable. Retries use
  backoff via `run_after`, up to `max_attempts`. `priority` puts on-demand work first.
- Failures are recorded per stage: run error, `processing_failures`, analysis error.

### Alternatives
- Jobs as the run record: one table, but lineage would be tied to the queue implementation.
- Check-then-insert deduplication: race condition under concurrency.
- Holding row locks for the whole job: long transactions block VACUUM and hide progress.

### Consequences
- (+) Queue can be swapped; duplicates prevented by the database; crash recovery without long transactions.
- (−) Some duplicated status between jobs and domain records; workers must send heartbeats.

---

## ADR-019: Database conventions

- **Status:** Accepted · **Date:** 2026-11-13

### Decision
- Postgres schemas per layer: `raw`, `core`, `analysis`, `ops`.
- Fixed value sets as `text` + `CHECK`, not native `ENUM` or lookup tables.
- `bigint` identity PKs; `timestamptz` in UTC; snake_case plural names; constraint names from a
  SQLAlchemy naming convention; `text` over `varchar(n)`; `jsonb`.
- FKs `RESTRICT` by default, `CASCADE` only parent → own children within a layer; no soft deletes in V1.

### Alternatives
- Single `public` schema with prefixes: simpler, but layers would be a naming habit only and
  permissions would be per table.
- Native ENUMs: values cannot be removed or reordered without recreating the type.
- Lookup tables: flexible, but a join for every label.

### Consequences
- (+) Layers visible in SQL; per-layer permissions possible; schema changes are ordinary migrations.
- (−) Slightly longer names; Alembic must be configured for multiple schemas.

---

## ADR-020: Alembic migrations, run as a separate explicit step

- **Status:** Accepted · **Date:** 2026-11-13

### Context
The schema must evolve in a reviewed, repeatable way across laptop, CI and future
deployments. Several processes (API, worker, possibly replicas) share one database.

### Decision
- Alembic manages the schema. Migration files are in `migrations/versions/`, named
  `YYYY_MM_DD-<rev>_<slug>.py` with sequential revision IDs (`0001`, `0002`, …). Each new
  revision file is formatted and linted by ruff automatically.
- The database URL comes from application settings (`CHESS_DATABASE_URL`), not `alembic.ini`.
- Migrations run **only as an explicit step**, never inside the API or worker:
  - locally: `uv run alembic upgrade head`
  - compose: a one-off `migrate` service; `api` starts only after it exits successfully
  - CI: `alembic upgrade head` + `alembic check` against the service Postgres
  - future deployments: a release step before new code starts
- The whole upgrade runs in one transaction (Postgres transactional DDL).
- Tests build schemas in throwaway databases and verify upgrade → downgrade → upgrade, a single
  head, and that models match migrations.

### Alternatives
- **Migrate at API startup:** convenient, but concurrent processes race; the app user needs DDL
  privileges; a broken migration crash-loops the app.
- **Hand-written SQL scripts / `metadata.create_all()`:** no version tracking or downgrades;
  `create_all` cannot change existing tables.

### Consequences
- (+) Schema changes are reviewed in PRs and applied identically everywhere.
- (+) App processes can later run with a database role that has no DDL rights.
- (+) A failing migration rolls back fully and blocks the app from starting with the wrong schema.
- (−) One more command in the local workflow (`alembic upgrade head` after pulling).

---

## ADR-021: HTTP API conventions (`/api/v1`)

- **Status:** Accepted · **Date:** 2026-11-15

### Context
Phase 5 adds the first business endpoints. The dashboard (Phase 7) and possibly other
clients will depend on them, so URL shape, versioning, pagination and error behaviour
must be decided before anything relies on them.

### Decision
- Business endpoints live under **`/api/v1`**. `/health` and `/health/ready` stay
  unversioned because infrastructure checks them, not clients.
- **Long-running work is asynchronous:** `POST /api/v1/players/{username}/sync` returns
  `202 Accepted` with the job and a `Location: /api/v1/jobs/{id}` header; clients poll
  `GET /api/v1/jobs/{id}`. A duplicate request returns the active job (`created: false`).
- **Pagination:** `limit` (1–100, default 50) and `offset`, plus `total` in the response.
- **The platform is a query parameter**, `?platform=chesscom` by default, so a second
  platform can be added without changing any existing URL.
- **Response models** (`api/schemas.py`) are separate from database models: tables can
  change without breaking clients, and internals (lineage ids, worker ids, leases) are
  not exposed. Read SQL lives in `storage/repositories/queries.py`, not in routes.
- **Errors:** invalid input → `422` before any database access (e.g. username pattern,
  `limit` range, unknown `time_class`); unknown player, game or job → `404` with a hint.
- No authentication in V1 (accounts arrive in V4); the API listens on localhost only.

### Alternatives
- **Unversioned URLs:** shorter, but an incompatible change breaks every client at once.
- **Cursor (keyset) pagination:** scales to millions of rows and is stable while new
  games arrive, but is more complex and cannot jump to page N. Offset is fine for
  thousands of games per player and can be replaced in a `v2` of the endpoint.
- **Platform in the path** (`/players/{platform}/{username}`): explicit, but longer URLs
  for a single-platform V1.
- **Synchronous sync endpoint:** simpler for clients, but holds an HTTP request open for
  10–25 s (minutes for engine analysis) and fails with the connection.

### Consequences
- (+) Clients get immediate responses; work survives client disconnects.
- (+) The contract can evolve (`/api/v2`) without breaking existing clients.
- (−) Clients must poll job status (push notifications could be added later).
- (−) Deep offset pages get slower on very large tables (not a V1 concern).

---

## ADR-022: Worker runtime and job execution

- **Status:** Accepted · **Date:** 2026-11-15

### Context
ADR-003 chose a Postgres-backed queue with a separate worker process; ADR-018 designed
the `ops.jobs` table. Phase 5 implements how jobs are claimed, kept alive, retried and
stopped, which determines reliability when workers crash or are restarted.

### Decision
- **Claiming:** `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)`, ordered
  by priority, then `run_after`. Concurrent workers never take the same job or wait for
  each other.
- **Polling:** when idle the worker waits ~2 s between checks; when busy it claims the
  next job immediately. `LISTEN/NOTIFY` can be added later for lower latency.
- **One job at a time per worker process**; scale by running more workers
  (`docker compose up --scale worker=N`).
- **Leases:** a claimed job holds `locked_until`; a background thread renews it every
  lease / 3 (lease 60 s). Workers periodically requeue jobs whose lease expired (their
  worker crashed). Heartbeat, complete and fail only apply while the worker still holds
  the lease, so a worker that lost its job cannot overwrite the new owner's result.
- **Failure classes:** `PermanentJobError` (unknown player, malformed payload, unknown job
  type) → `failed` immediately. Anything else (Chess.com outage, unexpected errors) is
  retried with backoff 30 s → 2 min → 8 min, up to `max_attempts` (3).
- **All times come from the database clock** (`now()`), never from worker machines.
- **Graceful shutdown:** the first SIGINT/SIGTERM lets the current job finish, a second
  one exits at once (the job is recovered via its lease). Compose gives the worker a
  60 s stop grace period.
- **Triggers:** on-demand only in V1 (API, CLI). Scheduled syncing is deferred (V2).
- **CI end-to-end test:** the full compose stack runs against a stdlib Chess.com stub
  (`tests/e2e/`); the test drives it over HTTP. CI never calls the real Chess.com.

### Alternatives
- **Celery/RQ with Redis:** mature, but adds a broker to run and monitor; job state would
  live outside the database we already back up and query.
- **Threads or async concurrency inside one worker:** more throughput per process, but
  harder to reason about; engine analysis is CPU-bound and better scaled by processes.
- **Fixed retries for every error:** wastes attempts on errors that can never succeed.

### Consequences
- (+) Crashed workers lose no work; duplicate execution is prevented in SQL.
- (+) Scaling out is a deployment change (more workers), not a code change.
- (+) Job status is queryable with plain SQL and exposed through the API.
- (−) Up to ~2 s latency before an idle worker starts a job.
- (−) A job longer than the lease must keep heartbeating; a stalled heartbeat thread
  would let the job be recovered and run twice (handlers are idempotent, so this is safe).


---

## ADR-023: Stockfish distribution and engine adapter

- **Status:** Accepted · **Date:** 2026-11-23

### Context
Analysis results depend on the exact engine build (ADR-004). The engine must be
available to the worker container, to CI, and to developers, and the version that
produced each result must be recorded.

### Decision
- **Pinned official release, verified by checksum.** `scripts/install-stockfish.sh`
  downloads Stockfish 19's official universal Linux build (x86-64 or arm64) and rejects
  it unless its SHA-256 matches the release's published digest. The Dockerfile runs it in
  a separate `engine` build stage and copies only the binary into the runtime image; the
  CI test job runs the same script (cached by the script's contents). Locally:
  `brew install stockfish`.
- **The engine's own identity is recorded**, read from its UCI `id name`
  (`Stockfish 19` → `stockfish`, `19`), never from configuration, so a different local
  build can't be mislabelled.
- **Adapter:** python-chess's `chess.engine` (already a dependency) behind our `Engine`
  protocol (`engine/base.py`). Scores are converted to White's point of view; a crashed
  process becomes `EngineError`. `evaluate_game` sends `ucinewgame` (clears the hash) at
  the start of every game, so a game's evaluations never depend on what was analysed
  before it.
- **Terminal positions are not searched:** checkmate is stored as mate 0 (the side to
  move is mated), stalemate / insufficient material / 75-move / fivefold as 0 cp.
  Claimable draws (threefold, 50-move) are searched normally.
- **Defaults:** 300,000 nodes per position, 64 MB hash, 1 thread, MultiPV 1. Measured on
  an Apple M-series core: ≈ 0.5M nodes/s, ≈ depth 18, ≈ 0.6 s per position, ≈ 40 s for an
  average 65-ply game.
- **Tests:** unit tests use a scripted `FakeEngine`; `tests/engine` (marker `engine`)
  runs the real binary, skipped locally if none is installed, required in CI.

### Alternatives
- **Debian's `stockfish` package:** older, and the version changes with the base image.
- **Compiling from source in Docker:** fully controllable, but slower builds and more
  moving parts, unnecessary now that official Linux arm64 builds exist.
- **Time-limited search:** see ADR-004 (not reproducible).

### Consequences
- (+) Docker, CI and (via Homebrew) local development run the same engine version, and
  every result names the engine that produced it.
- (+) The downloaded binary is verified before use.
- (−) The runtime image grows by ~100 MB (the binary embeds its neural network).
- (−) A Homebrew upgrade can move local development to a newer version than Docker; this
  is visible (different `engine_version`, hence a different analysis config), not silent.
- (−) Upgrading Stockfish means updating the version and both checksums in one script.

---

## ADR-024: Move classification by win-probability loss (classifier v1)

- **Status:** Accepted · **Date:** 2026-11-23

### Context
Each move needs a label (best … blunder) derived from engine evaluations of the position
before and after it. The same centipawn swing means very different things depending on
the position: +900 → +600 still wins, 0 → −300 is a serious error. The method was
deferred in Phase 2 (ADR-017 made classifications versioned and re-derivable).

### Decision
- **Win probability.** Each score becomes White's expected win %:
  `50 + 50 · (2 / (1 + e^(−0.00368208 · cp)) − 1)` (the curve Lichess publishes), with
  cp clamped to ±1000. Mate for a side = 100 % for it; `mate 0` = the side to move lost.
- **Loss** = the mover's win % before the move − after it, floored at 0, rounded to 0.01.
  Both players' moves are classified. The rounded loss is stored as `eval_loss` and is
  what the thresholds compare, so every label can be re-derived from its row.
- **Labels** (in order of precedence):
  - `forced`: the only legal move;
  - `best`: the engine's first choice in the position before;
  - otherwise by loss: `excellent` < 2 ≤ `good` < 10 ≤ `inaccuracy` < 20 ≤ `mistake`
    < 30 ≤ `blunder`.
- **`CLASSIFIER_VERSION = 1`.** Changing the curve, thresholds or labels means a new
  version (new rows, no engine re-run). Allowed labels are a DB `CHECK` (migration 0004).
- **No `brilliant` / `great` in V1.** Both depend on how good the *second-best* move was
  (a sacrifice that is the only good move), which needs MultiPV 2 (planned for V2).
- **These are our classifications.** They follow published, documented rules and are not
  a reproduction of Chess.com's (proprietary) labels; numbers will differ.

### Alternatives
- **Centipawn-loss thresholds (e.g. 50 / 100 / 300):** simple, but call harmless moves in
  decided positions blunders and need special cases for mate scores.
- **Heuristic brilliant / great detection without MultiPV:** labels we couldn't defend.
- **Expected-points models trained on rating:** more accurate per rating band, but needs
  data and a model we don't have.

### Consequences
- (+) Labels match intuition in winning, losing and equal positions; mate needs no special case.
- (+) Thresholds can be tuned later without re-running Stockfish.
- (−) The curve is not rating-aware: a 10 % drop is judged the same at 800 and at 2400.
- (−) With MultiPV 1 a non-engine move that is equally good is `excellent`, not `best`.


---

## ADR-025: Analysis jobs and the analysis API

- **Status:** Accepted · **Date:** 2026-11-23

### Context
Analysis (ADR-023, ADR-024) runs ≈ 40 s per game, so it belongs in the worker (ADR-022).
ADR-004 asks for the latest 20 games to be analysed after each sync and every other game
on demand. An analysis config includes the engine's exact version, which only a running
engine reports.

### Decision
- **One `analyse_game` job per game**, payload `{"game_id": N}`, `dedup_key`
  `analyse:<game_id>`. The job names no config: the worker that runs it analyses with
  its own engine and settings, so the API never needs to know the engine version.
- **Priorities** (lower runs first): syncs and on-demand analyses `100`, auto-analysis
  `200`. Re-requesting a job that is still queued at a lower priority moves it up (one
  conditional `UPDATE`, so a job being claimed at that moment is never changed).
- **Auto-analysis** happens in the sync handler, after a successful sync: of the
  player's 20 newest games (`CHESS_AUTO_ANALYSE_LATEST_N`), those without a fresh,
  successful analysis by the worker's config are queued. It is a fixed *window*: older
  games are not pulled in to fill it, so a sync never queues more than 20 analyses and
  the history is never backfilled by accident. If the engine cannot start, the sync
  still succeeds and the skip is logged.
- **Failures:** unknown game or malformed payload → permanent; engine failure →
  retryable, and the worker's engine process is discarded so the retry starts a new one.
- **One engine process per worker**, started on the first analysis and reused (its hash
  is cleared per game, ADR-023), closed when the worker stops.
- **API:** `POST /api/v1/games/{id}/analysis` → `202` + job. `GET …/analysis` returns
  the game's most useful analysis: a fresh, successful one if any, else the most
  recently updated (so a running or failed one is visible). Evaluations and labels are
  returned only when it succeeded; labels come from the newest classifier version stored.
  `404` for an unknown game and for a game never analysed.
- **End-to-end:** the CI e2e stack analyses the synced stub games with the real Stockfish
  from the image (20k nodes for speed). The e2e stack is its own compose project
  (`chess-analytics-e2e`, API on 18000, no host database port), so it can never attach
  to, or `down -v`, the development database.

### Alternatives
- **Dedup per (game, config):** precise, but the API would have to know the engine
  version; with one engine build per deployment it adds nothing.
- **"20 newest *unanalysed* games" instead of a window:** each sync would reach further
  back, quietly analysing the whole history (≈ 13 min per 20 games per worker).
- **Queueing auto-analysis from the API or in `sync_player`:** the API has no engine, and
  the sync service would gain an engine dependency it doesn't need.
- **A new engine process per job:** simpler, but ~0.5 s start-up per game and no reuse.

### Consequences
- (+) Duplicate requests never run an analysis twice; opening a game moves it ahead.
- (+) The cost of a sync is bounded: at most 20 analyses, all behind interactive work.
- (+) Scaling analysis is `--scale worker=N` (one core each).
- (−) Workers with different engine builds would analyse with different configs; both
  results are kept (ADR-017), and `GET` prefers the fresh one.
- (−) A game outside the latest 20 has no analysis until someone requests it; analytics
  must state coverage (ADR-004).

---

## ADR-026: Player analytics: accuracy, opening families, coverage

- **Status:** Accepted · **Date:** 2026-11-23

### Context
Phase 7 turns stored games and analyses into statistics: results, rating history,
openings, accuracy and errors (the README's V1 set). ADR-008 puts analytics SQL in
`analytics/`; ADR-004 requires analysis-based numbers to state their coverage.

### Decision
- **Computed at read time** with PostgreSQL (`analytics/queries.py`): nothing is stored,
  so a new classifier or analysis is reflected immediately. Every query takes the same
  filters: time class and an inclusive UTC date range.
- **Accuracy %.** Move accuracy = `103.1668 · e^(−0.04354 · loss) − 3.1669`, clamped to
  0–100 (Lichess's published curve), on the stored win-% loss (ADR-024). Game accuracy =
  **(arithmetic mean + harmonic mean) / 2** over the player's moves, forced moves
  excluded; in the harmonic mean a move counts as at least 1. Overall accuracy is the
  mean of game accuracies (each game counts once).
- **Which analysis counts:** per game, the fresh successful one (ADR-017), the newest
  engine config if several, labelled by the newest classifier version stored for it.
- **Coverage** (analysed games / games matching the filters) is returned with every
  accuracy and error statistic.
- **Opening families.** Chess.com's names are per variation (660 names over 2,400 games).
  The family is the name up to its first `Opening/Defense/Defence/Game/Gambit/Attack/System`
  word (52 families); grouping by full name is also available.
- **Rating history:** one point per time class and UTC day (the rating after the last
  rated game that day).

### Alternatives
- **Plain mean of move accuracies:** simplest, but generous: on the 3 games where
  Chess.com also reports accuracy, it read 16–20 points higher (mean+harmonic: 6–13).
- **Harmonic mean only:** dominated by the single worst move.
- **Lichess's exact method** (adds weights by position volatility): closer still, but
  needs sliding windows over evaluations; not worth it before we compare more games.
- **Precomputed statistics tables:** faster reads, but must be invalidated on every new
  analysis or classifier version; unnecessary at this data size (milliseconds per query).
- **ECO codes for openings:** only 113 codes, but a code covers unrelated names and is
  less readable; it is shown alongside instead (most common code in the group).

### Consequences
- (+) Statistics are always consistent with the latest data; no refresh jobs.
- (+) Accuracy reacts to blunders without one move ruining a game.
- (−) Our accuracy is not Chess.com's (their formula is private) and will differ.
- (−) Opening families depend on Chess.com's naming; a proper classification from the
  moves (planned for V2) would work across platforms.
- (−) Queries scan the player's games on each request; fine for thousands of games, to be
  revisited (materialised views) if needed.

---

## ADR-027: Dashboard as a separate project in `frontend/`

- **Status:** Accepted · **Date:** 2026-11-23

### Context
ADR-005 chose Streamlit and HTTP-only communication with the backend. Streamlit brings
pandas, pyarrow and friends (several hundred MB) that the API and worker don't need, and
"never import the backend" should be hard to break by accident.

### Decision
- **`frontend/` is its own uv project** (`package = false`, own `uv.lock`), with its own
  Dockerfile and a `dashboard` compose service (port 8501, waits for a healthy API). The
  backend is not one of its dependencies, so importing it fails: the boundary is enforced
  by construction, not by review.
- **One client module** (`dashboard/api.py`) maps endpoints to methods and HTTP failures
  to `NotFound` / `ApiError`; pages never build URLs. Pure `dashboard/tables.py` shapes JSON
  for charts (Altair). Responses are cached for 60 s (`st.cache_data`).
- **Tests:** the client against a scripted transport; pages with Streamlit's `AppTest`
  against a fake API; and an `e2e` test rendering the pages against the e2e stack's real
  API, so the fake can't drift from real responses (the contract check). CI runs all three;
  root `ruff` lints both projects (`src = ["src", "frontend"]`).

### Alternatives
- **Same project with a `frontend` dependency group:** one lock file and simpler setup,
  but Streamlit's dependencies would be resolved with the backend's, and the import rule
  only enforced by a test.
- **uv workspace:** shares one lock across members, which is what we want to avoid here.
- **Generated API client from OpenAPI:** less hand-written code, but a build step and
  generated code for a dozen endpoints; the contract test covers drift instead.

### Consequences
- (+) The API/worker image is unchanged; the dashboard can be replaced (ADR-005: likely
  in V4) without touching the backend.
- (+) A change to an API response that breaks the dashboard fails CI.
- (−) Two projects to keep updated (dependencies, Python version).
- (−) The dashboard image is large (~800 MB, mostly pandas/pyarrow via Streamlit).
