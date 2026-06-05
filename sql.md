# SQL Inventory

Every piece of hand-written SQL in the codebase. Originally generated 2026-06-04; last refreshed 2026-06-05 after the DB-per-entity refactor (TODO bullet 3).

After the per-entity-repository refactor, **all production data queries** live as SQLAlchemy expressions on the repository classes (`entities/*/repository.py`). The remaining `text()` calls are the documented carve-outs only.

## Summary

- Total remaining `text(...)` calls: **4**
  - `scripts/reset_db.py:90` — `DROP TABLE IF EXISTS alembic_version`
  - `tests/conftest.py:75` — `SELECT 1 FROM pg_database WHERE datname = :n`
  - `tests/conftest.py:81` — `CREATE DATABASE "ttt_test"` (interpolated; see Mitigation below)
  - `tests/conftest.py:113` — `TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE`
- Plus 2 `sa.text("CURRENT_TIMESTAMP")` server defaults in `alembic/versions/c19b6e2bf955_initial_schema.py` (always allowed — server-default expressions).
- Plus 1 test-side catalog query: `tests/test_reset_db.py` uses `func.to_regclass("public.bots")` etc. via the ORM `func` namespace (not raw `text()`), checking whether tables exist after reset.

There is **no hand-written `text()` SQL** in `web/`, `entities/`, `db/`, `runner/`, or `messaging/`. The leaderboard / bot-family CTE queries live in `entities/bot/repository.py` as `select(...).cte(...).scalar_subquery()` chains. Every other query is a plain ORM `select(Model)`, `session.add(Model(...))`, attribute assignment, or `session.execute(delete(Model))`.

## Where queries live now (the inventory)

### `entities/bot/repository.py` — `BotRepository`

All return Bot-shaped rows.

- `by_id(bot_id)` — `select(Bot).where(Bot.id == ...)`
- `by_ids([id, ...])` — `select(Bot).options(undefer(Bot.source)).where(Bot.id.in_(...))` (the `undefer` opt-in is what avoids per-bot lazy-load round-trips in `runner/dispatch.py`)
- `by_versioned_name(name)` — `select(Bot).where(Bot.versioned_name == ...)`
- `all()` — `select(Bot)`
- `list_for_homepage()` — `select(Bot.versioned_name, Bot.submitted_at).order_by(Bot.submitted_at.desc())`
- `owner_token(base_name)` — `select(Bot.owner_token).where(Bot.base_name == ...).limit(1)`
- `next_version(base_name)` — `select(func.max(Bot.version)).where(Bot.base_name == ...)`
- `create(...)` — `session.add(Bot(...))` + `await session.commit()`
- `leaderboard()` — multi-CTE chain (`select(...).cte("latest_per_family")`, then `.cte("latest_bot")`, then 6 correlated `.scalar_subquery()` columns rolled into a `stats` CTE). The lifetime-W/L NOT EXISTS uses `.correlate(latest_bot, Match)` to force correlation through two subquery levels.
- `family(base_name)` — `select(Bot...)` with 4 correlated COUNT scalar subqueries.

### `entities/match/repository.py` — `MatchRepository`

The `_match_select()` module-private helper builds the shared join (Match outer-joined to three `Bot.__table__.alias(...)` copies for X / O / winner names) used by `by_id`, `list_all`, and `list_for_bot`.

- `by_id(match_id, *, bot_base_name=None)` — optional family-membership filter
- `list_all()` — `_match_select()` + `.order_by(Match.played_at.desc())`
- `list_for_bot(base_name)` — adds `or_(bx.c.base_name == ..., bo.c.base_name == ...)` to the shared select
- `record(bot_x_id, bot_o_id, result)` — `session.add(Match(...))` + per-`Move` `session.add(...)`

### `entities/move/repository.py` — `MoveRepository`

- `for_match(match_id)` — `select(Move.move_number, Move.board_state, Move.error, Bot.versioned_name).join(Bot).where(Move.match_id == ...).order_by(Move.move_number)`

### `scripts/seed_example_bots.py`

- DELETE-then-INSERT cycle via `session.execute(delete(Move|Match|Bot))` (3 deletes) + `BotRepository.create(...)` (one per source file). Pure ORM.

### `tests/conftest.py` insert helpers

- `db_insert_bot`, `db_insert_match`, `db_insert_move` — `session.add(Model(...))` followed by `flush() / commit()` on an `AsyncSession`. Async helpers (called via `await`).

### `tests/test_*.py` assertion SELECTs

All use `select(Model.field, ...)` against `entities.*.model` classes — no `text()`.

## The 4 remaining `text()` calls — why they stay

### `scripts/reset_db.py:90` — `DROP TABLE IF EXISTS alembic_version`

Drops Alembic's bookkeeping table. The table isn't part of `Base.metadata` (it's owned by Alembic, not our ORM), so `Base.metadata.drop_all(...)` doesn't reach it. There is no ORM construct for a one-off `DROP TABLE` against an arbitrary identifier — `text()` is the right tool.

### `tests/conftest.py:75` — `SELECT 1 FROM pg_database WHERE datname = :n`

Postgres system catalog query — checks whether `ttt_test` exists before issuing `CREATE DATABASE`. `pg_database` isn't an ORM model and shouldn't be. The query is parameterized via `{"n": TEST_DB_NAME}` so there's no interpolation.

### `tests/conftest.py:81` — `text(f'CREATE DATABASE "{TEST_DB_NAME}"')`

The only **interpolated** SQL in the codebase. Mitigation: `TEST_DB_NAME` is a module-level constant (`"ttt_test"`), not user input. `CREATE DATABASE` in Postgres doesn't accept parameter binding for the database identifier, so f-string interpolation is unavoidable. Documented inline.

### `tests/conftest.py:113` — `TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE`

Per-test data reset. The closest ORM form would be three `delete(Model)` executes, which (a) is slower, (b) doesn't reset the identity sequences, and (c) runs as multiple statements rather than one atomic op. `TRUNCATE` is DDL, not data; out-of-scope per the original `text()` policy.

## Async note

Every `text()` call above runs through an async path:

- `scripts/reset_db.py:90` is inside `_drop_all_tables()`, an `async def` invoked via `asyncio.run(main())` at the script entrypoint.
- `tests/conftest.py:75` and `:81` run via the sync `_ensure_test_database_exists()` helper. That helper is intentionally sync (psycopg2) because it bootstraps the test database itself *before* the async session machinery comes up. It's the only sync DB path that remains in the codebase.
- `tests/conftest.py:113` runs inside `async with eng.begin() as conn: await conn.execute(...)` — the truncate fires on an `AsyncConnection`.

The only sync DB connection in production code or scripts: none. The only sync DB connection in tests: the `_ensure_test_database_exists` admin connection.
