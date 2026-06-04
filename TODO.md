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

- [x] **Async ORM on Postgres** — FastAPI uses SQLAlchemy 2.x async with `asyncpg`. `aiosqlite` is removed. The runner uses a SQLAlchemy sync engine via `psycopg2`.
- [x] **Alembic-managed schema** — `schema.sql` is gone; `db/models/` + `alembic/versions/` own the schema. Server defaults use `CURRENT_TIMESTAMP` for portability.
- [x] **Test coverage backstop** — 100% line coverage on `web/`, `db/`, and `runner/` with the `sys.monitoring` tracer.

## Highest priority — up next

Apply the same shape the `web/` package got (thin entrypoints, logic split into purpose-built modules) to the rest of the codebase. Each module ends up doing one thing; testable bits are pulled out of any `__main__`-style wiring.

- [ ] **Refactor the messaging code.** `messaging/__init__.py` is currently the only `__init__.py` in the project with logic in it — env-var resolution, a mutable module-level singleton (`_queue`), and two functions (`get_queue`/`set_queue`). Move all of that into a dedicated module (e.g., `messaging/client.py`) and leave `messaging/__init__.py` empty. Also split `rabbitmq.py` and `rpc.py` so connection setup / publish helpers / RPC client are each one file, with the singleton injected (or replaced with a context-manager pattern) rather than a global.
- [ ] **Refactor the runner code.** `runner/orchestrator.py` mixes AMQP plumbing, the game loop, DB persistence, and signal handling in one ~150-line file — pull each concern into its own module so the entrypoint becomes a short "glue these pieces together" file. Apply the same pattern to `runner/turn_worker.py` for consistency, even though it's smaller.
- [ ] **Refactor the DB code.** `db/database.py` is a grab bag of session setup + every query in the app (selects + the multi-CTE leaderboard `text(...)` block + insert helpers + `record_match`). Split into `db/session.py` (engine/session plumbing), `db/queries/<topic>.py` (one module per domain — bots, matches, moves, leaderboard) so a reader can find "where do leaderboard queries live" without scrolling 250 lines.
- [ ] **Replace raw SQL with SQLAlchemy expressions.** Every `text("...")` block that references model-defined columns or tables becomes a Core/ORM expression, so column references type-check against `db/models/` (today raw SQL is invisible to `ty` — rename `Bot.versioned_name` and the SQLAlchemy code fails at type-check time but the `text(...)` blocks silently keep referencing the old name and only fail at run time, with a generic Postgres error). Scope:
  - **`db/database.py`** — convert `_LEADERBOARD_SQL` (lines 137–199) and `_BOT_FAMILY_SQL` (lines 207–~234) using CTEs (`select(...).cte(...)`) + correlated scalar subqueries (`.scalar_subquery().correlate(...)`).
  - **`scripts/seed_example_bots.py`** — the SELECT / DELETE / INSERT statements all reference model tables and columns; convert to ORM.
  - **`tests/`** — every `text(...)` that names a model table or column gets converted (covers `tests/conftest.py`'s insert helpers, all the `SELECT ... FROM bots/matches/moves` assertion queries in `tests/test_*.py`, the `UPDATE bots SET source = ...` in `tests/test_orchestrator.py`, etc.).
  - **Out of scope** — pure DDL/admin without model column refs is fine to leave as `text()`: `TRUNCATE bots, matches, moves`, `DROP TABLE IF EXISTS alembic_version`, `CREATE DATABASE`, `SELECT 1 FROM pg_database`, server-default expressions in Alembic migrations like `sa.text("CURRENT_TIMESTAMP")`.
  - **Policy going forward** — `text()` with a model column or table name in it is a code smell. If you reach for it, justify it in the same change or refactor.

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
