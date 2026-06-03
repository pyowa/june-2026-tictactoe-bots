# Go-Forward Plan: Azure Deployment

Migration plan to move the app from a single-host SQLite + local Docker setup to an event-driven Azure architecture. Ordered so each step is independently testable, with Azure provisioning happening only after the local refactors land.

## Target architecture

| Concern | Service | Notes |
|---|---|---|
| Web app | Azure Container Apps | Containerized FastAPI, scale-to-zero |
| Database | Azure Database for PostgreSQL Flexible Server | B-series for the event scale |
| Bot file storage | Azure Blob Storage | Replaces the local `bots/` directory |
| Match queueing | Azure Service Bus | One message per `(bot_x, bot_o)` pair |
| Match execution | Azure Container Apps Jobs | KEDA scales jobs from Service Bus depth — each match is one job execution |
| Secrets | Azure Key Vault | DB string, storage keys, Service Bus connection |
| Logs / metrics | Application Insights | OpenTelemetry instrumentation for FastAPI |
| CI/CD | GitHub Actions → `az containerapp update` | Last step, not first |

## Data flow

```text
Browser ─upload─► Web (Container App)
                    ├─► Blob (bot .py)
                    ├─► Postgres (bot row)
                    └─► Service Bus (enqueue unplayed pairs)
                                     │
                          KEDA scaler ─► Container Apps Job
                                           ├─► fetch bots from Blob
                                           ├─► run match in job container
                                           └─► write result to Postgres
Browser ─poll─► Web (Container App) ─► Postgres
```

The runner stops polling. Pairs are enqueued at submission time; the job consumer drains the queue.

## Foundation already in place

These weren't separate line items in the original plan but are prerequisites the upcoming Azure work depends on, and they're done:

- [x] **Async ORM** — `aiosqlite` is now driven through SQLAlchemy 2.x async. The DB layer in `db/database.py` is engine-agnostic; swapping to Postgres is a connection-string change at the engine-creation boundary.
- [x] **Alembic-managed schema** — `schema.sql` is gone; `db/models/` + `alembic/versions/` own the schema. The SQLAlchemy types are portable; regenerating migrations against Postgres would produce equivalent DDL.
- [x] **Test coverage backstop** — 100% line coverage on `web/`, `db/`, and `runner/` with `sys.monitoring` tracer. Gives a solid regression net for the engine swap.

## Migration steps

### Phase 1 — Local refactors (no Azure required)

- **SQLite → Postgres** (partially done)
  - [x] Swap `aiosqlite` driver for SQLAlchemy 2.x async
  - [x] Move schema definition from raw SQL to ORM models managed by Alembic
  - [ ] Add a Postgres service to `docker-compose.yml` and switch the engine URL via env var
  - [ ] Update `tests/conftest.py` to spin up a Postgres test DB (testcontainers is the cleanest path)
  - [ ] Regenerate the initial Alembic migration against Postgres to capture any type differences
- [ ] **Abstract bot storage** behind a `BotStore` interface with two implementations:
  - `LocalBotStore` — writes to `bots/` (current behavior, for dev).
  - `BlobBotStore` — writes to Azure Blob (used in production).
  - Select via env var.
- [ ] **Event-driven runner.** Replace `find_unplayed_pairs` polling with a queue consumer.
  - On bot submission, web enqueues one message per `(bot_x_id, bot_o_id)` pair (including self-pair).
  - Runner consumes one message → runs one match → writes result → acks message.
  - For local dev use a Service Bus emulator or RabbitMQ in `docker-compose.yml`.
- [ ] **Two Dockerfiles** — one for web, one for the job runner. Multi-stage builds with `uv` for fast installs.

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
- Migrating the SQLite-using tests to mocks — keep them hitting a real Postgres.

## Cost estimate

Event-scale (low traffic, scale-to-zero idle): **~$20–40/mo**

- Postgres B1ms: ~$15
- Service Bus Basic: ~$0.05/M operations
- Blob (LRS, small): <$1
- Container Apps idle: ~$0; per-execution billing for jobs
- App Insights: free tier covers small volume

AKS stretch variant adds **~$70+/mo** for the cluster + node pool.
