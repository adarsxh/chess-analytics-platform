# Data Model

> **Status:** placeholder. Tables are designed in the dedicated data-modelling phase,
> after Phase 1. This file records the principles the schema must satisfy.

## Principles

1. **Natural identity at every layer**
   - Player: `(platform, platform_username)`, with the username normalised to lowercase.
   - Game: `(platform, platform_game_id)`, so a game is stored once (ADR-007).
2. **Idempotent writes:** every ingestion and processing step is an upsert on its
   natural key; re-running a step never creates duplicates.
3. **Raw is preserved:** source payloads are stored as received, with retrieval
   timestamp, ingestion run reference and content hash. Processed rows reference the raw
   row they came from (lineage).
4. **Versioned processing:** processed rows carry `parser_version`; analysis rows belong
   to an analysis run that records engine and classifier versions plus full
   configuration (ADR-004).
5. **Analysis is separate from games:** multiple analysis runs can exist for the same
   game, so re-analysis adds data rather than overwriting it.
6. **Incremental ingestion:** archive fetches are logged (URL, status, ETag /
   Last-Modified, content hash) so unchanged months are skipped.
7. **Schema evolution** is managed through Alembic migrations, never manual DDL.

## Layers → tables (to be designed)

| Layer | Candidate tables |
|---|---|
| Reference | `players` |
| Raw | `raw_games`, `archive_fetches`, `ingestion_runs` |
| Processed | `games`, `game_moves` |
| Analysis | `analysis_configs` / `analysis_runs`, `game_analyses`, `move_evaluations` |
| Operations | `jobs` |
