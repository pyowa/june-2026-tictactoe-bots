# Go-Forward Plan: Azure Deployment

Migration plan to move the app from a single-host (local Postgres + local Docker) setup to an event-driven Azure architecture. Ordered so each step is independently testable, with Azure provisioning happening only after the local refactors land.

## Target architecture

| Concern | Service | Notes |
|---|---|---|
| Web app | Azure Container Apps | Containerized FastAPI, scale-to-zero |
| Database | Azure Database for PostgreSQL Flexible Server | B-series for the event scale |
| Bot file storage | (no separate service — source lives in the `bots.source` BYTEA column on Postgres) | Sized for event volume; ~10 KB per bot |
| Match queueing | Azure Service Bus | One message per `(bot_x, bot_o)` pair |
| Match orchestration | Azure Container Apps (long-running worker, KEDA-scaled) | Consumes the match queue, drives the per-turn RPC loop, records results |
| Per-turn bot execution | N Azure Container Apps (one per supported Python version) | Each is the same code on `python:X.Y`; consumes its own turn queue and runs the bot as a subprocess |
| Secrets | Azure Key Vault | DB string, storage keys, Service Bus connection |
| Logs / metrics | Application Insights | OpenTelemetry instrumentation for FastAPI |
| CI/CD | GitHub Actions → `az containerapp update` | Last step, not first |

## Data flow

```text
Browser ─upload─► Web (Container App)
                    ├─► Postgres (bot row + source bytes)
                    └─► matches.todo  (one message per unplayed pair)

           matches.todo ─► Orchestrator (Container App, KEDA-scaled)
                              │  fetches source bytes for both bots from Postgres
                              │  for each turn:
                              ├──► RPC turn.py3.11.requests  ◄─► Worker py3.11
                              ├──► RPC turn.py3.12.requests  ◄─► Worker py3.12
                              ├──► ... (one queue + worker per supported version)
                              │
                              └──► Postgres (matches + moves)

Browser ─poll─► Web (Container App) ─► Postgres
```

The orchestrator is Python-version-agnostic — it just drives the game loop and dispatches each turn to the right per-version worker queue via RPC (correlation IDs on a reply queue). Workers run the bot as a subprocess inside their own container, so per-turn isolation is preserved without nested Docker.

## Foundation already in place

These weren't separate line items in the original plan but are prerequisites the upcoming Azure work depends on, and they're done:

- [x] **Async ORM on Postgres** — FastAPI uses SQLAlchemy 2.x async with `asyncpg`. `aiosqlite` is removed. After the DB-per-entity refactor every consumer (web, runner, scripts, tests) is async-only; the only sync DB code path left is the test conftest's admin-bootstrap (`CREATE DATABASE ttt_test` via psycopg2, listed as a dev dependency).
- [x] **Alembic-managed schema** — `schema.sql` is gone; `entities/*/model.py` + `alembic/versions/` own the schema. Server defaults use `CURRENT_TIMESTAMP` for portability.
- [x] **Test coverage backstop** — 100% line coverage on `web/`, `db/`, `entities/`, `runner/`, `messaging/`, and `scripts/` with the `sys.monitoring` tracer.

## Highest priority — up next

### Warm pods per match (k8s performance)

Currently the dispatcher creates one k8s Job per turn — up to 9 pod lifecycles per game, each paying ~2–3 s of scheduling + container-start overhead. The fix is to create one Pod per bot at match start, keep it alive for all turns in the match, drive turns via HTTP, then delete both pods at match end. Pod startup cost drops from `9 × 2` to `2` per game.

- [x] **Add an HTTP turn server to the bot-runner image.** Replace the one-shot `entrypoint.sh` with a small Python HTTP server (stdlib `http.server`, no extra deps). Endpoint: `POST /turn` accepts JSON `{"symbol": "X", "board": "..."}`, runs the bot source (from `SOURCE_B64` env var) as a subprocess, and returns `{"board": "..."}` or `{"error": "..."}`. `GET /health` returns 200. Expose port 8080.
- [x] **Update NetworkPolicy.** Allow ingress on port 8080 from the dispatcher pod (`app: dispatcher`) to bot-runner pods. All other ingress stays denied.
- [x] **Add per-match pod lifecycle to the dispatcher (`dispatcher/pods.py`).** Functions: `build_pod_manifest`, `wait_for_pod_ready`, `get_pod_ip`, `request_turn`, `delete_pod`. Top-level entry point in `dispatcher/match_runner.py`: `run_match_with_pods(core_v1, image_x, image_o, source_x_b64, source_o_b64, ...) -> MatchResult` — creates two pods, drives the full game loop via HTTP, deletes both pods in a `finally` block.
- [x] **Move the game loop into the dispatcher.** The dispatcher consumes a `match.requests` queue (carries both bots' source_b64, runtime keys, and correlation ID). It runs `run_match_with_pods`, then publishes the `MatchReply`. The orchestrator becomes a thin fan-out that enqueues match requests and records the replies.
- [x] **Remove per-turn RPC infrastructure.** Retired the `turn.requests` queue, the `TurnRequest` / `TurnReply` contracts, `runner/turn_worker.py`, `runner/match_loop.py`, `runner/bot_subprocess.py`, and `dispatcher/jobs.py`. The `runner/dispatch.py` sends a single match-level RPC to `match.requests`.

---

Apply the same shape the `web/` package got (thin entrypoints, logic split into purpose-built modules) to the rest of the codebase. Each module ends up doing one thing; testable bits are pulled out of any `__main__`-style wiring.

- [x] **Refactor the messaging code.** `messaging/__init__.py` is empty; env-var + factory live in `messaging/client.py`. Global singleton replaced with FastAPI `Depends(get_queue)` + per-app lifespan; tests substitute via `app.dependency_overrides`. `rpc.py` split into `rpc_client.py` (caller) and `rpc_server.py` (`serve_rpc` wiring). `rabbitmq.py` left as one 38-line class — splitting further into "connection" + "publish" was discussed and rejected as over-engineering for the size.
- [x] **Refactor the runner code.** `runner/orchestrator.py` is now a thin entrypoint (`run()` + `__main__` only); the per-turn RPC loop lives in `runner/match_loop.py`, end-to-end match handling (fetch sources, drive loop, persist) lives in `runner/dispatch.py`. `runner/turn_worker.py` got the same split: thin broker entrypoint, with the bot tmpfile/subprocess invocation and RPC marshalling in `runner/bot_subprocess.py`.
- [x] **Refactor the DB code into per-entity packages with Repository + DI, and go async-only.** Replace the current `db/database.py` grab bag and `db/models/{entity}.py` parallel tree with a domain-shaped layout that co-locates schema and queries per entity. Target layout:

  ```text
  entities/
  ├── bot/
  │   ├── model.py        # class Bot(Base) — columns only
  │   └── repository.py   # class BotRepository — every query that returns Bot-shaped rows
  ├── match/
  │   ├── model.py
  │   └── repository.py
  └── move/
      ├── model.py
      └── repository.py
  db/
  ├── base.py             # DeclarativeBase (moved from db/models/base.py)
  └── session.py          # engine + session factory + get_db_session DI dependency
  ```

  Repositories take an `AsyncSession` in their constructor; their methods are async; queries are SQLAlchemy 2.x `select(...)` expressions. Cross-entity queries (leaderboard, list-matches-for-bot) live with the entity they return (leaderboard returns Bot-shaped rows → BotRepository; list-for-bot returns Match-shaped rows → MatchRepository).

  FastAPI routes get repositories via `Depends`: `get_bots(session = Depends(get_db_session)) -> BotRepository`, plus siblings for Match and Move. Tests substitute via `app.dependency_overrides[get_bots] = ...` when they want fakes; otherwise the real `BotRepository` is used end-to-end against the test DB.

  **Everything goes async.** Drop `create_sync_engine`, drop the psycopg2 runtime dep. Tests, conftest helpers (`db_insert_bot/match/move` become async), and scripts (`seed_example_bots.py`, `reset_db.py`) all convert to async. The only remaining sync path is alembic's offline mode (used only for `alembic --sql` generation, not normal workflow).

  Why this layout: a new contributor lands on `entities/bot/` and finds both the schema and every available operation in one place. The `db/` directory becomes just session/engine plumbing. Per CLAUDE.md "Database query style" the ORM patterns are already locked in; this refactor moves the *organization* to match the conceptual model.
- [x] **Replace raw SQL with SQLAlchemy expressions.** All hand-written `text("...")` blocks referencing model tables/columns are gone. The leaderboard and bot-family queries now live as `select(...).cte(...)` chains with `.scalar_subquery()` for the correlated COUNTs on `BotRepository` (`entities/bot/repository.py`); `scripts/seed_example_bots.py` uses `BotRepository.create(...)` / `session.execute(delete(Model))` / `select(Bot)` everywhere; the conftest insert helpers and every test assertion SELECT are ORM expressions. `Bot.source` is deferred at the model level so `select(Bot)` doesn't pull BYTEA payloads. `ty` now type-checks every column reference across `web/ db/ entities/ runner/ messaging/ scripts/ tests/`. The CLAUDE.md "Database query style" section codifies the conventions. Remaining `text()` calls are all documented exceptions: `tests/conftest.py` (pg_database catalog + CREATE DATABASE + TRUNCATE), `scripts/reset_db.py` (the user-specified DDL carve-out for `alembic_version`), and Alembic migrations (`sa.text("CURRENT_TIMESTAMP")` server defaults).
- [ ] **Audit test mocks.** Walk through `mocks.md` (generated catalog of every `MagicMock` / `AsyncMock` / `monkeypatch.setattr` / `app.dependency_overrides` / custom fake/stub in the test suite). For each entry decide: (a) is the mock load-bearing — i.e., would removing it force the test to hit a real broker/process/network? (b) would replacing the mock with a real implementation give us stronger coverage at acceptable cost? Goal: shrink the surface area where we're mocking *our own code* (those mocks pin the wiring, not behavior — and silently rot when the real thing changes). Keep mocks for true boundaries (AMQP channels, subprocess, urlopen). Document conclusions inline in `mocks.md` or remove that file once everything's been reviewed.
- [ ] **Make the messaging layer broker-agnostic so RabbitMQ is one implementation, not the only one.** Today `aio_pika` leaks beyond `messaging/`: `runner/orchestrator.py` and `runner/turn_worker.py` both call `aio_pika.connect_robust(BROKER_URL)` directly; `messaging/rpc_client.py` and `messaging/rpc_server.py` accept `aio_pika.AbstractChannel` as a parameter and construct `aio_pika.Message` objects inline. A future swap to Azure Service Bus (Phase 2 of the Azure plan already names it as the production target), AWS SQS, NATS, or a simple in-memory bus for tests would require touching all four files. Target shape: every `aio_pika` import lives inside `messaging/`. Orchestrator/worker receive a broker-agnostic connection object from a `messaging.client` factory (`make_connection() -> BrokerConnection`), and the RPC client/server are typed against that abstraction. The existing `Queue` protocol is the model — extend it with connection-lifecycle and request/response primitives. Two concrete payoffs: (a) when the Azure work starts, only one new module (`messaging/servicebus.py`) is needed; (b) an in-memory implementation removes the requirement that RabbitMQ be running for `poe test`, which would speed the suite and simplify CI.

## Mutation testing — test-suite coverage gaps

These items come from a manual mutation-testing audit: 144 candidate mutations attempted, 38 of them slipped through with the suite still green. The codebase already has 100% line coverage and the tests do exercise behavior — but coverage only proves the lines *ran*, not that an assertion would notice if a comparison were flipped, a `WHERE` clause dropped, or a literal swapped. Each item below pins one such mutation by suggesting a specific assertion that would have caught it.

Most of Tier A is concentrated in the leaderboard CTE — the six correlated COUNT subqueries (clean wins, forfeit wins, draws, losses, lifetime wins, lifetime losses) are all currently asserted only via aggregated totals or via "non-zero" smoke checks. Pinning roughly six explicit COUNT values across the existing leaderboard tests (one match per category, asserting the exact integer count on both the winner and the loser's row) would close most of Tier A in a single session.

### Acceptance test — remaining mutmut survivors (top priority)

These mutants survived the latest run against `entities/bot/repository.py`. Fix them before moving on to Tier A.

- [ ] **mutmut_46** — drops `latest_per_family.c.max_v == Bot.version` from the `latest_bot` join, so all versions appear in the leaderboard instead of only the latest. Fix: assert that Alpha v1 does NOT appear as a separate row in the leaderboard (only `AlphaV2` should appear for the Alpha family).
- [ ] **mutmut_196** — drops `or_(bx.base_name == lb_base, bo.base_name == lb_base)` participation filter from `lifetime_losses`, so every non-cat match in the DB is counted as a loss for every family. Fix: add a bot with `lifetime_losses=0` alongside families that have real losses, and assert `lifetime_losses == 0` remains correct when the filter is removed by mutation.
- [ ] **mutmut_197** — drops `or_(bx.base_name != lb_base, bo.base_name != lb_base)` intra-family exclusion from `lifetime_losses`. Fix: add an Alpha v1 vs Alpha v2 match and assert it does NOT inflate `lifetime_losses` for the Alpha family.
- [ ] **mutmut_226 / mutmut_228** — drop `Match.winner_id.is_(None)` from the `lifetime_losses` OR condition. Fix: add a null-winner non-cat match (if possible given the schema) or document that this branch is unreachable and mark with `# pragma: no mutate`.
- [ ] **mutmut_284** — secondary `ORDER BY submitted_at ASC` dropped. Fix: give two bots with equal wins different `submitted_at` timestamps and assert the earlier-submitted bot appears first.
- [ ] **mutmut_2, 15, 45, 50, 82, 113, 115** — survivors with no diff shown; need individual investigation via `mutmut show` to determine if they are equivalent or true gaps.

### Tier A — silent data-bug risks

- [ ] **`entities/bot/repository.py:131`** — leaderboard `clean_wins` `Match.result.in_(("x_wins", "o_wins"))` can have either literal dropped silently. Fix: add a leaderboard test that records one `x_wins` match and one `o_wins` match for the same family and assert `clean_wins == 2` on that row (and `== 0` on opponents).
- [ ] **`entities/bot/repository.py:130`** — leaderboard `clean_wins` `Match.winner_id == lb_id` can be mutated to `!=` without test failure. Fix: assert `clean_wins` is exactly the count for the winning bot's family AND that the losing bot's family row shows `clean_wins == 0` for the same fixture.
- [ ] **`entities/bot/repository.py:140`** — leaderboard `forfeit_wins` `Match.result.in_(("x_forfeit", "o_forfeit"))` can drop `"x_forfeit"` silently. Fix: add a leaderboard test with an x_forfeit victory and assert the forfeit_wins count is correct for both sides.
- [ ] **`entities/bot/repository.py:140`** — leaderboard `forfeit_wins` `Match.result.in_(("x_forfeit", "o_forfeit"))` can drop `"o_forfeit"` silently. Fix: add a leaderboard test with an o_forfeit victory and assert `forfeit_wins == 1` on the winner row.
- [ ] **`entities/bot/repository.py:148`** — leaderboard `draws` `or_(Match.bot_x_id == lb_id, Match.bot_o_id == lb_id)` can drop either side silently (a draw is only credited when the bot appears as that specific side). Fix: add a leaderboard test with two draws — one where the family is X and one where it is O — and assert `draws == 2`.
- [ ] **`entities/bot/repository.py:149`** — leaderboard `draws` `Match.result == "cat"` can be mutated to `!= "cat"` or a different literal. Fix: assert that a non-draw match with the same participants does NOT increment `draws`.
- [ ] **`entities/bot/repository.py:157`** — leaderboard `losses` `or_(Match.bot_x_id == lb_id, Match.bot_o_id == lb_id)` can drop either side. Fix: add a leaderboard test where the family loses once as X and once as O; assert `losses == 2` on the loser's row.
- [ ] **`entities/bot/repository.py:158`** — leaderboard `losses` `Match.result != "cat"` can flip to `==` silently. Fix: include a draw involving the same bot and assert it does NOT count as a loss.
- [ ] **`entities/bot/repository.py:159`** — leaderboard `losses` `or_(Match.winner_id.is_(None), Match.winner_id != lb_id)` can drop either branch. Fix: cover both forfeit (winner present, opponent) and unfinished/null-winner edge cases in tests and assert `losses` matches expectation in both.
- [ ] **`entities/bot/repository.py:179`** — leaderboard `lifetime_wins` `bw.base_name == lb_base` can be mutated to `!=`. Fix: pin `lifetime_wins` to an exact count for a multi-version family where one specific version wins and another does not, asserting the value rolls up to the family.
- [ ] **`entities/bot/repository.py:180`** — leaderboard `lifetime_wins` exclusion `or_(bx.base_name != lb_base, bo.base_name != lb_base)` (the "no pure intra-family wins" filter) can drop either side or flip operator. Fix: add an intra-family match (FooV1 vs FooV2) and assert it does NOT inflate Foo's `lifetime_wins`.
- [ ] **`entities/bot/repository.py:198`** — leaderboard `lifetime_losses` inner `winner_not_in_family` subquery `bw_inner.id == Match.winner_id` can be mutated. Fix: a multi-version-family fixture losing to an external bot should pin `lifetime_losses == 1`; flipping the join would change the count.
- [ ] **`entities/bot/repository.py:199`** — leaderboard `lifetime_losses` inner `bw_inner.base_name == lb_base` can be mutated to `!=`, inverting the NOT EXISTS. Fix: include a match where the family beats itself and a match where it loses to an outsider; assert `lifetime_losses == 1`, not 2.
- [ ] **`entities/bot/repository.py:212`** — leaderboard `lifetime_losses` "family participated" filter `or_(bx.base_name == lb_base, bo.base_name == lb_base)` can drop either side. Fix: include matches where the family appears on X-only and on O-only and assert `lifetime_losses` reflects both.
- [ ] **`entities/bot/repository.py:263`** — `family()` `forfeit_wins` `Match.result.in_(("x_forfeit", "o_forfeit"))` can drop either literal (separate query from the leaderboard one, same risk). Fix: add a `BotRepository.family(...)` test that records one x_forfeit and one o_forfeit and asserts `forfeit_wins == 2` on the right per-version row.
- [x] **`entities/match/repository.py:73`** — `list_for_bot` `or_(bx.c.base_name == base_name, bo.c.base_name == base_name)` can drop either side, so matches where the family was X (or O) silently disappear. Fix: add a `list_for_bot` test that records one match with the family as X and one as O; assert the result includes both.

### Tier B — silent contract drift

- [x] **`entities/bot/repository.py:72`** — `BotRepository.create(..., python_version: str = "3", ...)` default can be mutated to any other string and tests still pass. Fix: add a unit test that calls `create(...)` without `python_version` and asserts the persisted row has `python_version == "3"`.
- [ ] **`entities/bot/repository.py:235`** — leaderboard ordering `order_by((stats.c.clean_wins + stats.c.forfeit_wins).desc(), stats.c.submitted_at.asc())`. Both clauses (and the `+` between the two win columns) can be mutated. Fix: add a tie-breaking test where two families have equal wins but different `submitted_at`; assert the earlier-submitted bot ranks higher. Add a second test where one family has more `forfeit_wins` and another has more `clean_wins` (same total); assert they sort equal then by submitted_at.
- [x] **`entities/bot/repository.py:298`** — `family()` `order_by(Bot.version.desc())` can flip to `.asc()` silently. Fix: assert that a multi-version family is returned newest-first (e.g. assert `result[0].version > result[-1].version`).
- [x] **`entities/match/repository.py:65`** — `list_all` `order_by(Match.played_at.desc())` can flip. Fix: insert two matches with explicit `played_at` timestamps and assert the newer match appears at index 0.
- [x] **`entities/match/repository.py:74`** — `list_for_bot` `order_by(Match.played_at.desc())` can flip. Same fix: pin the ordering with a two-match fixture.
- ~~**`messaging/queue.py:4`**~~ — removed in Phase 1 refactor (`MATCHES_QUEUE` / `enqueue_match` dead code deleted).
- [x] **`messaging/client.py:5-6`** — both `DEFAULT_BROKER_URL = "amqp://guest:guest@localhost:5672/"` (the literal) and the env-var name in `os.environ.get("RABBITMQ_URL", ...)` can be mutated independently. Fix: one test that imports `messaging.client` with `RABBITMQ_URL` unset and asserts `BROKER_URL == "amqp://guest:guest@localhost:5672/"`, plus a second test that monkeypatches `RABBITMQ_URL` (specifically — not a different name) and asserts the override takes effect.
- [x] **`runner/dispatch.py:MATCH_TIMEOUT`** — stale: `runner/dispatch.py` no longer exists (removed in k8s migration).
- [ ] **`messaging/rpc_server.py:24`** — `await channel.set_qos(prefetch_count=1)` — the literal `1` can be mutated. The fairness contract (one in-flight message per worker, no head-of-line blocking) is invisible to tests today because the function is `# pragma: no cover`. Fix: factor the QOS setup out of the wiring shim so it can be unit-tested with a `MagicMock` channel asserting `set_qos.assert_called_once_with(prefetch_count=1)`.

### Tier C — weak assertions

- [x] **`web/utils.py:53`** — `extract_bot_name` `stripped[5:].strip()` — the `5` (length of `"name:"`) can be mutated to 4 or 6 and current tests still pass because the bot names used in fixtures happen to survive both slices. Fix: add a test whose bot-name line has a character that would be lost (e.g. `"name:F"`) and assert the returned name is exactly `"F"`.
- [x] **`web/utils.py:79`** — `extract_python_version` `stripped[7:].strip()` — same off-by-one risk on the `7` (length of `"python:"`). Fix: add a test with `"python:3"` (no space) and assert the returned version is exactly `"3"`.
- [ ] **`web/submit.py:49`** — `source_bytes.decode("utf-8", errors="replace")` — the `errors="replace"` mode is mutable to `"ignore"` or `"strict"`. The contract (we want bad bytes preserved as `�`, not raised, not silently dropped) isn't tested. Fix: upload a bot with an invalid UTF-8 byte in a non-critical position and assert the persisted source still contains a `U+FFFD` replacement character.
- [ ] **`web/submit.py:87`** — `secrets.token_hex(32)` — the `32` (64 hex chars) can be mutated. Fix: assert the minted owner_token is exactly 64 characters (and matches `^[0-9a-f]{64}$`).
- [ ] **`scripts/reset_db.py:90`** — `await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))` — the literal `"alembic_version"` can be mutated to any other table name and the reset still appears to succeed because the test infra doesn't assert this specific drop. Fix: in the reset-db test, assert that `alembic_version` does not exist in the catalog after `_drop_all_tables` runs.
- [x] **`dispatcher/match_runner.py:_forfeit_label`** — `_forfeit_label` returns `"x_forfeit"` if `player == "x"` else `"o_forfeit"`. Either literal can be mutated. Fixed: direct parametrized tests in `tests/test_dispatcher_match_runner.py`.
- [x] **`messaging/routing.py:13`** — `pick_python_version` `max(a, b, key=parse)` — the `max` can be mutated to `min`. Fix: add a test `pick_python_version("3.10", "3.12") == "3.12"` (a current test passes both orders but only checks "the result is in {a,b}", not which one).
- [x] **`runner/engine.py:33`** — `parse_board` `all(c in ("X", "O", ".") for c in cells)` — any of the three literals can be dropped from the tuple. Fix: add three explicit `parse_board` tests asserting that a row of all-X, all-O, and all-`.` each parse successfully (today only mixed rows are tested).

### Deliberately unkillable (defensive code, not bugs)

These three mutations survive because the protected code is either logically dead or explicitly excluded from coverage; flagging them so a future audit doesn't waste time re-investigating.

- **`messaging/rabbitmq.py:21`** — `if self._connection is None or self._connection.is_closed` — the entire `_ensure_connected` reconnect path is `# pragma: no cover` because exercising it requires a real broker dropping the connection mid-test. Mutations to the condition (e.g. dropping the `is_closed` check) survive by construction. Acceptable: the contract is asserted by the smoke test, not by unit tests.
- **`messaging/rabbitmq.py:36`** — `RabbitMQQueue.close()` — also `# pragma: no cover`; it's only reachable when an actual aio_pika connection has been opened, which doesn't happen in unit tests (every test substitutes a fake). Mutations survive; the production-only path is observed manually.
- **`db/session.py:32`** — `reconfigure()` `global` declaration is technically mutable (Python lets you drop `global` and create a local shadow), but the function is only called by `tests/conftest.py` to point at `ttt_test`; if it stopped working, every test would immediately fail to find any rows. The whole test suite IS the assertion; no targeted mutation test is meaningful.

## Migration steps

### Phase 1 — Local refactors (no Azure required)

- **SQLite → Postgres** (done)
  - [x] Swap `aiosqlite` driver for SQLAlchemy 2.x async
  - [x] Move schema definition from raw SQL to ORM models managed by Alembic
  - [x] Add a Postgres service to `docker-compose.yml` and switch the engine URL via `DATABASE_URL`
  - [x] Make timestamp defaults portable (`func.current_timestamp()`)
  - [x] Convert the runner from raw `sqlite3` to a SQLAlchemy sync engine
  - [x] Tests share the local `docker compose` Postgres but live in their own `ttt_test` database; per-test isolation via `TRUNCATE`. SQLite removed from the codebase entirely.
- [x] **Bot source moved into Postgres** (`bots.source` BYTEA). Web writes bytes on upload; the local `bots/` directory is now a debug-only mirror that the legacy polling runner still consumes. Workers will read source straight from the DB / RPC payload, no shared filesystem needed. Replaces the originally-planned `BotStore` abstraction since DB-resident source removes the need for it at this scale.
- **Move from polling to event-driven (RPC-over-queue)** — done in slices:
  - [x] **Broker + queue abstraction.** RabbitMQ in `docker-compose.yml`. Thin `Queue` interface with a RabbitMQ implementation. Web enqueues a `MatchJob(bot_x_id, bot_o_id, python_version)` per unplayed pair on bot submission.
  - [x] **Orchestrator + turn workers.** `runner/orchestrator.py` consumes `matches.todo`, fetches bot sources from Postgres, drives the per-turn RPC loop, persists results. `runner/turn_worker.py` consumes its per-version queue, runs the bot source as a subprocess, replies. Polling runner retired.
  - [x] **Multi-Python worker fleet in compose.** `docker-compose.yml` declares one `worker-pyX.Y` service per supported Python version (3.10, 3.11, 3.12, 3.13, 3.14), all built from the shared `worker` target with a per-service `PY_VERSION` build arg so the image's interpreter matches the `turn.pyXY.requests` queue it consumes. Each service is ~5 lines via a YAML anchor for the shared bits.
- [x] **Dockerfiles for web, orchestrator, and the worker base image** — single multi-stage `Dockerfile` with `web`, `orchestrator`, and `worker` build targets sharing a `uv`-installed base.
- [x] **Move all services to Kubernetes.** The full stack runs in a local kind cluster. `platform` namespace: postgres (StatefulSet), rabbitmq (Deployment), web (Deployment + NodePort 30000), match-scheduler (Deployment). `bots` namespace: dispatcher + bot pods. Docker Compose now runs only Postgres (for the test suite) and the mutmut profile. Migrations run as an init container on the web Deployment. `make reload-web` for fast web iteration.
- [ ] **Structured logging + cross-service traceability.** The system has too many moving pieces (web → broker → orchestrator → worker → broker → orchestrator → DB) for ad-hoc `print` statements to be debuggable. Every log line must carry the IDs that let us reconstruct what happened to a given match or bot:
  - **Match correlation ID** — generated at enqueue time in the web (UUID), carried on the `MatchJob` payload, threaded through every orchestrator + worker log line for that match, and persisted to the `matches` row (new column, e.g. `correlation_id`). The DB's auto-increment `matches.id` is fine for human reference but it's only known *after* the row is written — we need an ID that exists from "the moment this match was scheduled" so the enqueue, the consume, the turn-by-turn RPCs, and the final write are all joinable.
  - **Bot IDs** — already on every row, just need to consistently appear as structured fields (`bot_x_id`, `bot_o_id`) in every log line for the match.
  - **Per-turn detail** — each turn logs at least: `match_id`, `move_number`, `symbol`, the publishing side, the consuming side (worker), the subprocess outcome (success / timeout / runtime error), and the validation result. A reader should be able to grep one `match_id` and see all 9 (or fewer) turns in order across the orchestrator and worker logs.
  - **JSON-structured output** so the logs are parseable by App Insights / `jq` / `grep -E` without ambiguity. Suggested library: `structlog` (simple, async-friendly, plays well with OpenTelemetry context propagation).
  - **Bot-centric views** must be derivable from the same data: given a `bot_id`, all `match_id`s it appeared in (as X or O), and all moves it made.
  - Web's submission endpoint also logs upload events (`bot_name`, new `bot_id`, declared `python_version`, number of MatchJobs enqueued) — that's the first link in the trace chain.
  - Once this lands, the Phase 3 App Insights work becomes a pure plumbing exercise (configure the OTel exporter; the application logs already have the right shape).

### Phase 2 — Azure provisioning (portal/CLI, not IaC yet)

- [ ] Create resource group, Container Registry, Postgres Flexible Server, Storage account + Blob container, Service Bus namespace + queue, Key Vault, Log Analytics workspace + Application Insights.
- [ ] Push images to ACR.
- [ ] Deploy Container App (web) with min replicas = 0, max = 3.
- [ ] Deploy Container Apps Job (runner) with Service Bus scaler.
- [ ] Wire all secrets through Key Vault references in Container App config.
- [ ] Manual smoke test: upload a bot, watch it appear in the leaderboard.

### Phase 3 — Operational polish

- [ ] **CI/CD** — GitHub Actions: build → push to ACR → `az containerapp update`. Separate workflows for web and runner.
- [ ] **Observability** — wire `opentelemetry-instrumentation-fastapi`, ship traces/logs to App Insights. Add a dashboard for queue depth + match duration.
- [ ] **Bot sandboxing review** — the job container itself is the sandbox, but confirm CPU/memory limits in the job spec, set `read-only` root filesystem where possible, and drop network egress for bot subprocesses.
- [ ] **Bicep / Terraform** — codify the infrastructure once it's stable. Don't do this on day one.

## Stretch: AKS for K8s exposure

If you want explicit Kubernetes practice, replace **only the runner** with an AKS cluster running KEDA + a small worker Deployment consuming the same Service Bus queue. Keep the web on Container Apps. Cost goes up (~$70+/mo for the cluster vs. ~$0 idle for Container Apps Jobs), so treat this as a learning iteration after the Container Apps version is fully working.

## Explicit non-goals

- Azure Functions — bad fit for existing FastAPI.
- Cosmos DB — data is relational.
- API Management / Front Door / multi-region — overkill at this scale.
- Bicep/Terraform before manual provisioning works.
- Replacing tests' real DB hits with mocks — keep them hitting a real Postgres.

## Cost estimate

Event-scale (low traffic, scale-to-zero idle): **~$20–40/mo**

- Postgres B1ms: ~$15
- Service Bus Basic: ~$0.05/M operations
- Blob (LRS, small): <$1
- Container Apps idle: ~$0; per-execution billing for jobs
- App Insights: free tier covers small volume

AKS stretch variant adds **~$70+/mo** for the cluster + node pool.
