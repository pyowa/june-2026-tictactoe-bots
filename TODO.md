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
  - [ ] **Multi-Python worker fleet in compose.** Today we run one worker (py3) on the host. Promote to compose with one service per supported Python version (`matcher-py311`, `matcher-py312`, ...), all built from a shared `Dockerfile.worker` with a `PY_VERSION` build arg.
- [ ] **Dockerfiles for web, orchestrator, and the worker base image** — multi-stage builds with `uv` for fast installs.

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
