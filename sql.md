# SQL Inventory

Every piece of hand-written SQL in the codebase. Generated 2026-06-04.

## Summary

- Total occurrences: **30**
- By location:
  - production code: **2** (both in `db/database.py`)
  - scripts: **7** (6 in `scripts/seed_example_bots.py`, 1 in `scripts/reset_db.py`)
  - tests: **19** (across 6 test files, including 3 insert helpers in `tests/conftest.py`)
  - migrations: **2** (server-default `sa.text("CURRENT_TIMESTAMP")` only — explicitly out-of-scope per TODO)
- By category:
  - SELECT: **18** (1 production multi-CTE, 1 production multi-subquery, plus 16 in scripts/tests)
  - INSERT: **5** (3 in `tests/conftest.py` helpers, 1 in `scripts/seed_example_bots.py`, 1 ad-hoc in `tests/test_pages.py`)
  - UPDATE: **1** (`tests/test_orchestrator.py`)
  - DELETE: **3** (all in `scripts/seed_example_bots.py`)
  - DDL/admin: **5** (`CREATE DATABASE`, `TRUNCATE`, `DROP TABLE IF EXISTS`, two `sa.text("CURRENT_TIMESTAMP")` server defaults)
  - Interpolated (highest risk): **1** (`tests/conftest.py:61` — f-string `CREATE DATABASE`)
- References to model-defined tables/columns: **23** (the high-priority targets for SQLAlchemy-expression conversion per TODO bullet 4)
- Pure DDL/admin without model column refs: **5** (per TODO, fine to leave as `text()` — `TRUNCATE`, `DROP TABLE IF EXISTS alembic_version`, `CREATE DATABASE`, `SELECT 1 FROM pg_database`, `sa.text("CURRENT_TIMESTAMP")` in migrations)
- Special-case admin SELECTs that name model tables but only as bare identifiers passed to Postgres catalog functions: **1** (`tests/test_reset_db.py:223` — `to_regclass('public.bots'), ...`; these are *catalog* queries, not data queries — out-of-scope)

A note on what isn't here: SQLAlchemy Core/ORM expressions (`select(Bot).where(...)`, `Bot.__table__.alias(...)`, etc.) aren't catalogued — those are SQL *constructed* by the library and already type-check against the model classes. Examples that exist in this codebase but were skipped:

- `db/database.py` — `get_owner_token`, `get_next_version`, `insert_bot` (via `session.add`), `record_match` (via `session.add` for both `Match` and `Move`), `list_bots`, `_match_select`/`list_matches`/`get_match`, `get_moves` (full `select(...).join(...).where(...).order_by(...)` chains)
- `runner/orchestrator.py:118` — `select(Bot.id, Bot.source).where(Bot.id.in_([...]))`
- `web/utils.py:137` — `select(Bot.id, Bot.python_version)`
- `web/submit.py:118` — `select(Bot.id).where(Bot.versioned_name == name)`

This file lists only the **hand-written** SQL that bypasses that type-checking.

---

## Production code

### `db/database.py`

- **`db/database.py:139-201`** — `_LEADERBOARD_SQL` constant (module-level)
  - Category: SELECT (multi-CTE with 6 correlated scalar subqueries inside the `stats` CTE)
  - References model tables/columns: **yes** —
    tables: `bots`, `matches`
    columns: `bots.base_name`, `bots.version`, `bots.id`, `bots.versioned_name`, `bots.submitted_at`, `matches.bot_x_id`, `matches.bot_o_id`, `matches.winner_id`, `matches.result`
  - Caller: `get_leaderboard(session)` at `db/database.py:204-206`
  - Notes: Two CTEs (`latest_per_family`, `latest_bot`) feeding a third (`stats`). Per-bot stats include clean wins (`result IN ('x_wins','o_wins')`), forfeit wins (`'x_forfeit','o_forfeit'`), draws (`'cat'`), losses, and lifetime W/L counts that exclude intra-family matches via a `NOT EXISTS (SELECT 1 FROM bots bw ...)`. Ordered by `(clean_wins + forfeit_wins) DESC, submitted_at ASC`.
  - Refactor priority: **high** — TODO bullet 4 names this explicitly ("convert `_LEADERBOARD_SQL` (lines 137–199) using CTEs (`select(...).cte(...)`) + correlated scalar subqueries (`.scalar_subquery().correlate(...)`)").

- **`db/database.py:209-232`** — `_BOT_FAMILY_SQL` constant (module-level)
  - Category: SELECT (single statement with 4 correlated scalar subqueries)
  - References model tables/columns: **yes** —
    tables: `bots`, `matches`
    columns: `bots.versioned_name`, `bots.version`, `bots.submitted_at`, `bots.id`, `bots.base_name`, `matches.winner_id`, `matches.bot_x_id`, `matches.bot_o_id`, `matches.result`
  - Caller: `get_bot_family(session, base_name)` at `db/database.py:235-239` (parameterized via `{"base_name": base_name}`)
  - Notes: Per-version stats query (clean wins / forfeit wins / draws / losses), filtered by `b.base_name = :base_name`, ordered by `b.version DESC`. Same correlated-subquery shape as `_LEADERBOARD_SQL` minus the lifetime W/L columns.
  - Refactor priority: **high** — TODO bullet 4 names this explicitly ("`_BOT_FAMILY_SQL` (lines 207–~234)").

---

## Scripts

### `scripts/reset_db.py`

- **`scripts/reset_db.py:75`** — `conn.execute(text("DROP TABLE IF EXISTS alembic_version"))`
  - Category: DDL
  - References model tables/columns: **no** (`alembic_version` is Alembic's bookkeeping table, not a model)
  - Caller: `main()` in `reset_db.py`
  - Notes: Pure admin cleanup. Out-of-scope per TODO bullet 4.

### `scripts/seed_example_bots.py`

- **`scripts/seed_example_bots.py:31`** — `text("SELECT id, python_version FROM bots ORDER BY id")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `bots.id`, `bots.python_version`
  - Caller: `enqueue_all_pairs(engine, queue)` at lines 26-40
  - Notes: Drives the Cartesian-product enqueue loop. Trivial select; convert to `select(Bot.id, Bot.python_version).order_by(Bot.id)`.

- **`scripts/seed_example_bots.py:48`** — `conn.execute(text("DELETE FROM moves"))`
  - Category: DELETE
  - References model tables/columns: **yes** — table `moves`
  - Caller: `main()` (clears state before re-seeding)
  - Notes: Bare delete-all; converts to `session.execute(delete(Move))`.

- **`scripts/seed_example_bots.py:49`** — `conn.execute(text("DELETE FROM matches"))`
  - Category: DELETE
  - References model tables/columns: **yes** — table `matches`
  - Caller: `main()`
  - Notes: As above, for `Match`.

- **`scripts/seed_example_bots.py:50`** — `conn.execute(text("DELETE FROM bots"))`
  - Category: DELETE
  - References model tables/columns: **yes** — table `bots`
  - Caller: `main()`
  - Notes: As above, for `Bot`.

- **`scripts/seed_example_bots.py:72-75`** — `text("SELECT MAX(version) FROM bots WHERE base_name = :n")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `bots.version`, `bots.base_name`
  - Caller: `main()` (inside the per-source loop, before assigning a new version number)
  - Notes: Parameterized. Mirrors `get_next_version` in `db/database.py:65-70` which is already an ORM expression — could simply call that helper instead.

- **`scripts/seed_example_bots.py:79-94`** — triple-quoted `INSERT INTO bots (base_name, versioned_name, version, owner_token, python_version, source) VALUES (:b, :v, :ver, :t, :py, :src)`
  - Category: INSERT
  - References model tables/columns: **yes** — table `bots`; columns `base_name`, `versioned_name`, `version`, `owner_token`, `python_version`, `source`
  - Caller: `main()`
  - Notes: Plain parameterized insert. Convert to `session.add(Bot(...))` or `insert(Bot).values(...)`.

---

## Tests

### `tests/conftest.py`

- **`tests/conftest.py:56-59`** — `text("SELECT 1 FROM pg_database WHERE datname = :n")`
  - Category: SELECT (Postgres system catalog)
  - References model tables/columns: **no** (`pg_database` is a system catalog, not a model)
  - Caller: `_ensure_test_database_exists()`
  - Notes: Pure admin — out-of-scope per TODO.

- **`tests/conftest.py:61`** — `text(f'CREATE DATABASE "{TEST_DB_NAME}"')`
  - Category: DDL
  - References model tables/columns: **no**
  - Caller: `_ensure_test_database_exists()`
  - Notes: **Interpolated** (f-string), but the interpolated value is a module-level constant (`"ttt_test"`), not user input, and `CREATE DATABASE` doesn't accept parameter binding in Postgres anyway. Flagged here only because the task asks for interpolated SQL to be called out. Out-of-scope per TODO.

- **`tests/conftest.py:86-88`** — `text("TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE")`
  - Category: DDL/admin (TRUNCATE)
  - References model tables/columns: only as bare identifiers in a TRUNCATE
  - Caller: `engine` fixture (runs once per test for isolation)
  - Notes: TODO explicitly cites `TRUNCATE bots, matches, moves` as out-of-scope.

- **`tests/conftest.py:130-149`** — `db_insert_bot` helper; triple-quoted `INSERT INTO bots (base_name, versioned_name, version, owner_token, python_version, submitted_at) VALUES (:bn, :bn, 1, 'token', :pv, COALESCE(CAST(:sa AS timestamp), CURRENT_TIMESTAMP)) RETURNING id`
  - Category: INSERT (with RETURNING)
  - References model tables/columns: **yes** — table `bots`; columns `base_name`, `versioned_name`, `version`, `owner_token`, `python_version`, `submitted_at`, `id`
  - Caller: every test that needs a seeded bot (`test_pages.py`, `test_submission.py`, `test_orchestrator.py`, `test_seed_example_bots.py`, ...)
  - Notes: Highest-impact insert helper. The `COALESCE(CAST(:sa AS timestamp), CURRENT_TIMESTAMP)` shape can be expressed with `func.coalesce(cast(literal_column(":sa"), DateTime), func.current_timestamp())` or by computing the timestamp in Python. High-priority refactor target.

- **`tests/conftest.py:152-179`** — `db_insert_match` helper; triple-quoted `INSERT INTO matches (bot_x_id, bot_o_id, winner_id, result, played_at) VALUES (:bx, :bo, :w, :r, COALESCE(CAST(:pa AS timestamp), CURRENT_TIMESTAMP)) RETURNING id`
  - Category: INSERT (with RETURNING)
  - References model tables/columns: **yes** — table `matches`; columns `bot_x_id`, `bot_o_id`, `winner_id`, `result`, `played_at`, `id`
  - Caller: many tests in `test_pages.py`, `test_orchestrator.py`
  - Notes: Same shape as `db_insert_bot`. High-priority refactor target.

- **`tests/conftest.py:182-206`** — `db_insert_move` helper; triple-quoted `INSERT INTO moves (match_id, move_number, bot_id, board_state, error) VALUES (:m, :n, :b, :bs, :e)`
  - Category: INSERT
  - References model tables/columns: **yes** — table `moves`; columns `match_id`, `move_number`, `bot_id`, `board_state`, `error`
  - Caller: `test_pages.py` and others that seed move history
  - Notes: Plainest of the three insert helpers; convert to `session.add(Move(...))` or `insert(Move).values(...)`.

### `tests/test_pages.py`

- **`tests/test_pages.py:244-252`** — triple-quoted ad-hoc `INSERT INTO bots (base_name, versioned_name, version, owner_token, python_version, submitted_at) VALUES ('MyBot', 'MyBotV2', 2, 'tok', '3', '2024-01-02 10:00:00')`
  - Category: INSERT (with literal values — no parameter binding)
  - References model tables/columns: **yes** — table `bots`; columns `base_name`, `versioned_name`, `version`, `owner_token`, `python_version`, `submitted_at`
  - Caller: `test_leaderboard_shows_only_latest_version_per_family`
  - Notes: Inserts a v2 manually because the helper only does v1. Replaceable with `insert(Bot).values(...)`.

- **`tests/test_pages.py:451-473`** — `_insert_versioned` helper; triple-quoted `INSERT INTO bots (base_name, versioned_name, version, owner_token, python_version, submitted_at) VALUES (:b, :v, :ver, :t, '3', CAST(:sa AS timestamp)) RETURNING id`
  - Category: INSERT (with RETURNING)
  - References model tables/columns: **yes** — table `bots`; columns `base_name`, `versioned_name`, `version`, `owner_token`, `python_version`, `submitted_at`, `id`
  - Caller: bot-family-detail tests in `test_pages.py`
  - Notes: Duplicates `db_insert_bot`'s shape with explicit version control. Could be unified with `db_insert_bot` once both are converted to ORM.

### `tests/test_orchestrator.py`

- **`tests/test_orchestrator.py:187`** — `text("UPDATE bots SET source = :s WHERE id = :id")`
  - Category: UPDATE
  - References model tables/columns: **yes** — table `bots`; columns `source`, `id`
  - Caller: `_set_source` helper used by every `record_match`/`handle_match_message` test in this file
  - Notes: Convert to `update(Bot).where(Bot.id == bot_id).values(source=source)`. High-priority refactor target (TODO bullet 4 specifically calls this out).

- **`tests/test_orchestrator.py:229`** — `text("SELECT result, winner_id FROM matches")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `matches`; columns `result`, `winner_id`
  - Caller: `test_handle_match_message_persists_o_winning_result` (assertion query)
  - Notes: Tiny single-row assertion; convert to `select(Match.result, Match.winner_id)`.

- **`tests/test_orchestrator.py:261`** — `text("SELECT result, winner_id FROM matches")` (identical string to line 229)
  - Category: SELECT
  - References model tables/columns: **yes** — same as above
  - Caller: `test_handle_match_message_persists_cat_result`

- **`tests/test_orchestrator.py:292`** — `text("SELECT result, winner_id FROM matches")` (identical string)
  - Category: SELECT
  - References model tables/columns: **yes** — same as above
  - Caller: `test_handle_match_message_persists_result`

- **`tests/test_orchestrator.py:299-302`** — `text("SELECT move_number, bot_id, board_state FROM moves ORDER BY move_number")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `moves`; columns `move_number`, `bot_id`, `board_state`
  - Caller: `test_handle_match_message_persists_result`
  - Notes: Assertion query; convert to `select(Move.move_number, Move.bot_id, Move.board_state).order_by(Move.move_number)`.

- **`tests/test_orchestrator.py:346`** — `text("SELECT result, winner_id FROM matches")` (identical string)
  - Category: SELECT
  - References model tables/columns: **yes** — same as above
  - Caller: `test_record_match_x_forfeit_credits_o_as_winner`

- **`tests/test_orchestrator.py:372`** — `text("SELECT result, winner_id FROM matches")` (identical string)
  - Category: SELECT
  - References model tables/columns: **yes** — same as above
  - Caller: `test_record_match_o_forfeit_credits_x_as_winner`
  - Notes: With lines 229/261/292/346/372 all running the exact same assertion query, a converted version would also dedupe nicely.

### `tests/test_submission.py`

- **`tests/test_submission.py:152`** — `text("SELECT python_version FROM bots WHERE versioned_name = 'MyBot'")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `python_version`, `versioned_name`
  - Caller: `test_python_version_defaults_when_omitted`
  - Notes: Literal `'MyBot'` is embedded in the string (not bound). Convert to `select(Bot.python_version).where(Bot.versioned_name == "MyBot")`.

- **`tests/test_submission.py:169`** — `text("SELECT python_version FROM bots WHERE base_name LIKE 'V%' LIMIT 1")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `python_version`, `base_name`
  - Caller: `test_python_version_supported_versions_accepted` (parametrized)
  - Notes: Uses `LIKE 'V%'` — convert to `Bot.base_name.like("V%")` and `.limit(1)`.

- **`tests/test_submission.py:289`** — `text("SELECT source FROM bots WHERE versioned_name = 'MyBot'")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `source`, `versioned_name`
  - Caller: `test_upload_stores_source_bytes_in_db`

- **`tests/test_submission.py:303-306`** — `text("SELECT versioned_name, source FROM bots WHERE base_name = 'MyBot' ORDER BY version")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `versioned_name`, `source`, `base_name`, `version`
  - Caller: `test_resubmit_stores_each_version_separately_in_db`

### `tests/test_seed_example_bots.py`

- **`tests/test_seed_example_bots.py:86-89`** — `text("SELECT base_name, versioned_name, version, python_version FROM bots ORDER BY base_name")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `base_name`, `versioned_name`, `version`, `python_version`
  - Caller: `test_main_inserts_bots_and_enqueues_match_jobs`

- **`tests/test_seed_example_bots.py:129-132`** — `text("SELECT versioned_name, version FROM bots WHERE base_name = 'Foo' ORDER BY version")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `versioned_name`, `version`, `base_name`
  - Caller: `test_main_auto_versions_duplicate_names`
  - Notes: Literal `'Foo'` embedded. Convert to `select(Bot.versioned_name, Bot.version).where(Bot.base_name == "Foo").order_by(Bot.version)`.

- **`tests/test_seed_example_bots.py:160`** — `text("SELECT base_name FROM bots")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; column `base_name`
  - Caller: `test_main_skips_files_without_name_field`

- **`tests/test_seed_example_bots.py:185-187`** — `text("SELECT python_version FROM bots WHERE base_name = 'Weird'")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`; columns `python_version`, `base_name`
  - Caller: `test_main_falls_back_to_python_3_when_version_unsupported`

- **`tests/test_seed_example_bots.py:210`** — `text("SELECT COUNT(*) FROM bots")`
  - Category: SELECT
  - References model tables/columns: **yes** — table `bots`
  - Caller: `test_main_with_empty_directory_prints_and_returns`
  - Notes: Convert to `select(func.count()).select_from(Bot)`.

### `tests/test_reset_db.py`

- **`tests/test_reset_db.py:223-228`** — `text("SELECT to_regclass('public.bots'), to_regclass('public.matches'), to_regclass('public.moves'), to_regclass('public.alembic_version')")`
  - Category: SELECT (Postgres catalog function)
  - References model tables/columns: only as schema-qualified literal strings passed to `to_regclass()` — not as SQLAlchemy column references
  - Caller: `test_main_dropping_tables_runs_alembic_and_purges_queues` (asserts that all four tables are gone after `reset_db.main()`)
  - Notes: Edge case — the model table names appear in the string, but they're arguments to a Postgres catalog function, not the schema being queried. Hard to express as a typed SQLAlchemy expression (`to_regclass` isn't a model concept). Treating as out-of-scope per the spirit of the TODO ("pure DDL/admin without model column refs"). If converted, would use `func.to_regclass("public.bots")` etc.

---

## Migrations

### `alembic/versions/c19b6e2bf955_initial_schema.py`

- **`alembic/versions/c19b6e2bf955_initial_schema.py:32`** — `server_default=sa.text("CURRENT_TIMESTAMP")` on `bots.submitted_at`
  - Category: DDL (server default expression)
  - References model tables/columns: **no** (`CURRENT_TIMESTAMP` is a SQL built-in)
  - Caller: `upgrade()`
  - Notes: TODO explicitly cites `sa.text("CURRENT_TIMESTAMP")` server defaults in migrations as out-of-scope.

- **`alembic/versions/c19b6e2bf955_initial_schema.py:42`** — `server_default=sa.text("CURRENT_TIMESTAMP")` on `matches.played_at`
  - Category: DDL (server default expression)
  - References model tables/columns: **no**
  - Caller: `upgrade()`
  - Notes: Same as above. Out-of-scope.

### `alembic/versions/353c41ecc7b4_add_bot_source_column.py`

No `text()` / `op.execute()`. Pure `op.add_column` / `op.drop_column`.

### `alembic/versions/f3af65520232_drop_bot_file_path_column.py`

No `text()` / `op.execute()`. Pure `op.add_column` / `op.drop_column`.

### `alembic/env.py`

No hand-written SQL (no `text()`, no `op.execute()`, no `connection.execute(...)` of a literal string).

---

## Categorized index

### High-priority targets (reference model columns/tables — convert per TODO bullet 4)

Production:

- `db/database.py:139-201` — `_LEADERBOARD_SQL` (multi-CTE leaderboard query)
- `db/database.py:209-232` — `_BOT_FAMILY_SQL` (per-version bot family stats)

Scripts:

- `scripts/seed_example_bots.py:31` — `SELECT id, python_version FROM bots ORDER BY id`
- `scripts/seed_example_bots.py:48` — `DELETE FROM moves`
- `scripts/seed_example_bots.py:49` — `DELETE FROM matches`
- `scripts/seed_example_bots.py:50` — `DELETE FROM bots`
- `scripts/seed_example_bots.py:72-75` — `SELECT MAX(version) FROM bots WHERE base_name = :n`
- `scripts/seed_example_bots.py:79-94` — `INSERT INTO bots ...`

Test insert helpers (high impact — used by many tests):

- `tests/conftest.py:130-149` — `db_insert_bot` (`INSERT INTO bots ... RETURNING id`)
- `tests/conftest.py:152-179` — `db_insert_match` (`INSERT INTO matches ... RETURNING id`)
- `tests/conftest.py:182-206` — `db_insert_move` (`INSERT INTO moves ...`)
- `tests/test_pages.py:451-473` — `_insert_versioned` helper (`INSERT INTO bots ... RETURNING id`)

Test ad-hoc inserts/updates:

- `tests/test_pages.py:244-252` — ad-hoc `INSERT INTO bots ...` (literal values)
- `tests/test_orchestrator.py:187` — `UPDATE bots SET source = :s WHERE id = :id`

Test assertion SELECTs (each tiny, mostly identical, lots of duplication):

- `tests/test_orchestrator.py:229` — `SELECT result, winner_id FROM matches`
- `tests/test_orchestrator.py:261` — `SELECT result, winner_id FROM matches`
- `tests/test_orchestrator.py:292` — `SELECT result, winner_id FROM matches`
- `tests/test_orchestrator.py:299-302` — `SELECT move_number, bot_id, board_state FROM moves ORDER BY move_number`
- `tests/test_orchestrator.py:346` — `SELECT result, winner_id FROM matches`
- `tests/test_orchestrator.py:372` — `SELECT result, winner_id FROM matches`
- `tests/test_submission.py:152` — `SELECT python_version FROM bots WHERE versioned_name = 'MyBot'`
- `tests/test_submission.py:169` — `SELECT python_version FROM bots WHERE base_name LIKE 'V%' LIMIT 1`
- `tests/test_submission.py:289` — `SELECT source FROM bots WHERE versioned_name = 'MyBot'`
- `tests/test_submission.py:303-306` — `SELECT versioned_name, source FROM bots WHERE base_name = 'MyBot' ORDER BY version`
- `tests/test_seed_example_bots.py:86-89` — `SELECT base_name, versioned_name, version, python_version FROM bots ORDER BY base_name`
- `tests/test_seed_example_bots.py:129-132` — `SELECT versioned_name, version FROM bots WHERE base_name = 'Foo' ORDER BY version`
- `tests/test_seed_example_bots.py:160` — `SELECT base_name FROM bots`
- `tests/test_seed_example_bots.py:185-187` — `SELECT python_version FROM bots WHERE base_name = 'Weird'`
- `tests/test_seed_example_bots.py:210` — `SELECT COUNT(*) FROM bots`

### Out-of-scope per TODO (pure DDL/admin, no model column refs)

- `scripts/reset_db.py:75` — `DROP TABLE IF EXISTS alembic_version`
- `tests/conftest.py:56-59` — `SELECT 1 FROM pg_database WHERE datname = :n` (system catalog)
- `tests/conftest.py:61` — `CREATE DATABASE "ttt_test"` (DDL, can't be parameterized)
- `tests/conftest.py:86-88` — `TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE` (DDL)
- `tests/test_reset_db.py:223-228` — `SELECT to_regclass('public.bots'), to_regclass('public.matches'), to_regclass('public.moves'), to_regclass('public.alembic_version')` (Postgres catalog function, not a data query)
- `alembic/versions/c19b6e2bf955_initial_schema.py:32` — `sa.text("CURRENT_TIMESTAMP")` server default
- `alembic/versions/c19b6e2bf955_initial_schema.py:42` — `sa.text("CURRENT_TIMESTAMP")` server default

### Interpolated SQL (highest risk — flag for review)

- `tests/conftest.py:61` — `text(f'CREATE DATABASE "{TEST_DB_NAME}"')`
  - Mitigation: `TEST_DB_NAME` is a module-level constant (`"ttt_test"`), not user input. `CREATE DATABASE` does not accept parameter binding in Postgres, so f-string interpolation is the only option. Safe in practice but worth a comment in the code to make the reasoning explicit.

There is no other interpolated SQL in the codebase. All other parameter-bearing statements use `:name` placeholders bound via the second argument to `conn.execute(text(...), {...})`.

### Embedded literal values (parameterizable but currently hard-coded)

Some test SELECTs embed literal values (`'MyBot'`, `'Foo'`, `'Weird'`, `'V%'`) directly in the SQL string rather than parameter-binding them. Not a security issue (test fixtures, known inputs), but converting them to typed expressions will eliminate the foot-gun:

- `tests/test_submission.py:152` (`'MyBot'`)
- `tests/test_submission.py:169` (`'V%'`)
- `tests/test_submission.py:289` (`'MyBot'`)
- `tests/test_submission.py:303-306` (`'MyBot'`)
- `tests/test_seed_example_bots.py:129-132` (`'Foo'`)
- `tests/test_seed_example_bots.py:185-187` (`'Weird'`)
- `tests/test_pages.py:244-252` (multiple literal values in INSERT VALUES)
