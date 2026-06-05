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

- [x] **Refactor the messaging code.** `messaging/__init__.py` is empty; env-var + factory live in `messaging/client.py`. Global singleton replaced with FastAPI `Depends(get_queue)` + per-app lifespan; tests substitute via `app.dependency_overrides`. `rpc.py` split into `rpc_client.py` (caller) and `rpc_server.py` (`serve_rpc` wiring). `rabbitmq.py` left as one 38-line class — splitting further into "connection" + "publish" was discussed and rejected as over-engineering for the size.
- [x] **Refactor the runner code.** `runner/orchestrator.py` is now a thin entrypoint (`run()` + `__main__` only); the per-turn RPC loop lives in `runner/match_loop.py`, end-to-end match handling (fetch sources, drive loop, persist) lives in `runner/dispatch.py`. `runner/turn_worker.py` got the same split: thin broker entrypoint, with the bot tmpfile/subprocess invocation and RPC marshalling in `runner/bot_subprocess.py`.
- [ ] **Refactor the DB code.** `db/database.py` is a grab bag of session setup + every query in the app (selects + the multi-CTE leaderboard `text(...)` block + insert helpers + `record_match`). Split into `db/session.py` (engine/session plumbing), `db/queries/<topic>.py` (one module per domain — bots, matches, moves, leaderboard) so a reader can find "where do leaderboard queries live" without scrolling 250 lines.
- [x] **Replace raw SQL with SQLAlchemy expressions.** All hand-written `text("...")` blocks referencing model tables/columns are gone. `_LEADERBOARD_SQL` and `_BOT_FAMILY_SQL` in `db/database.py` are now `select(...).cte(...)` chains with `.scalar_subquery()` for the correlated COUNTs; `scripts/seed_example_bots.py` uses `session.add(Bot(...))` / `session.execute(delete(Model))` / `select(Bot)` everywhere; the conftest insert helpers and every test assertion SELECT are ORM expressions. `Bot.source` is deferred at the model level so `select(Bot)` doesn't pull BYTEA payloads. `ty` now type-checks every column reference across `web/ db/ runner/ messaging/ scripts/ tests/`. The CLAUDE.md "Database query style" section codifies the conventions. Remaining `text()` calls are all documented exceptions: `tests/conftest.py` (pg_database catalog + CREATE DATABASE + TRUNCATE), `scripts/reset_db.py` (the user-specified DDL carve-out), and Alembic migrations (`sa.text("CURRENT_TIMESTAMP")` server defaults).
- [ ] **Audit test mocks.** Walk through `mocks.md` (generated catalog of every `MagicMock` / `AsyncMock` / `monkeypatch.setattr` / `app.dependency_overrides` / custom fake/stub in the test suite). For each entry decide: (a) is the mock load-bearing — i.e., would removing it force the test to hit a real broker/process/network? (b) would replacing the mock with a real implementation give us stronger coverage at acceptable cost? Goal: shrink the surface area where we're mocking *our own code* (those mocks pin the wiring, not behavior — and silently rot when the real thing changes). Keep mocks for true boundaries (AMQP channels, subprocess, urlopen). Document conclusions inline in `mocks.md` or remove that file once everything's been reviewed.
- [ ] **Make the messaging layer broker-agnostic so RabbitMQ is one implementation, not the only one.** Today `aio_pika` leaks beyond `messaging/`: `runner/orchestrator.py` and `runner/turn_worker.py` both call `aio_pika.connect_robust(BROKER_URL)` directly; `messaging/rpc_client.py` and `messaging/rpc_server.py` accept `aio_pika.AbstractChannel` as a parameter and construct `aio_pika.Message` objects inline. A future swap to Azure Service Bus (Phase 2 of the Azure plan already names it as the production target), AWS SQS, NATS, or a simple in-memory bus for tests would require touching all four files. Target shape: every `aio_pika` import lives inside `messaging/`. Orchestrator/worker receive a broker-agnostic connection object from a `messaging.client` factory (`make_connection() -> BrokerConnection`), and the RPC client/server are typed against that abstraction. The existing `Queue` protocol is the model — extend it with connection-lifecycle and request/response primitives. Two concrete payoffs: (a) when the Azure work starts, only one new module (`messaging/servicebus.py`) is needed; (b) an in-memory implementation removes the requirement that RabbitMQ be running for `poe test`, which would speed the suite and simplify CI.

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
