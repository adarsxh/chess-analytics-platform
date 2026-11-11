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
- (−) Actions are pinned to major-version tags, not commit SHAs. That is convenient, but
  a compromised tag would affect us; consider SHA pinning plus Dependabot later.
- Future: a deployment workflow (CD) is a separate, later decision once a target is chosen.
