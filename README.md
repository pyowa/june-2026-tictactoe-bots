# Pyowa Tic-Tac-Toe Bot Competition

A web platform for the Iowa Python Users Group (Pyowa) bot battle event. Participants submit Python bots that compete in automated tic-tac-toe matches, with results tracked on a live leaderboard.

---

## Building a Bot

A bot is a single `.py` file. The runner invokes it once per move: it gets the current board on stdin and prints the updated board to stdout.

### Required docstring

The very first thing in your file must be a docstring with a `name:` field. Optionally, set `python:` to pin a Python version (defaults to the latest Python 3).

```python
"""
name: My Awesome Bot
python: 3.11
"""
```

Valid `python:` values are the actively supported Python releases: **`3.10`, `3.11`, `3.12`, `3.13`, `3.14`**. Omit the field and you'll get `3.14` (the latest). Anything else — a bare `3`, an unsupported `3.9`, `latest`, etc. — is rejected at upload.

### I/O protocol

**Stdin** — four lines: your symbol, then the 3×3 board (pipe-delimited, `X` / `O` / `.`):

```text
X
X|.|.
.|O|.
.|.|.
```

**Stdout** — the same board with exactly one new piece placed in an empty cell:

```text
X|.|.
.|O|.
.|X|.
```

### Forfeits

Your bot forfeits the match immediately if it:

- Produces no output, or output that isn't a valid 3×3 board
- Places more than one piece, places in an occupied cell, or places the wrong symbol
- Raises an unhandled exception
- Exceeds the per-move time limit

Forfeit wins are tracked separately from clean wins on the leaderboard.

### Example bot

```python
"""
name: Top-Left Bot
"""
import sys

data = sys.stdin.read().strip().splitlines()
symbol = data[0]
board = [row.split('|') for row in data[1:]]

for r in range(3):
    for c in range(3):
        if board[r][c] == '.':
            board[r][c] = symbol
            print('\n'.join('|'.join(row) for row in board))
            sys.exit(0)
```

### Submitting

Open the web UI at `http://localhost:8000` and upload your `.py` file. The first upload of a given name claims it and the site sets a cookie marking you as the owner. Re-uploading the same name (with the cookie) auto-increments the version: `MyBot` → `MyBotV2` → `MyBotV3`. All versions compete independently. Without the cookie, that name is locked to its original owner.

---

## Running the App

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Python 3.11+
- Docker (runs Postgres and RabbitMQ locally; also sandboxes each match)

### Setup

```bash
git clone <repo>
cd tic-tac-toe-event
uv sync --group dev

uv run poe up           # build + start the whole stack (db, rabbitmq, web, orchestrator, worker)
uv run poe migrate      # apply the schema against the running Postgres
```

Defaults: Postgres at `postgresql+asyncpg://ttt:ttt@localhost:5432/ttt`, RabbitMQ at `amqp://guest:guest@localhost:5672/`. Inside containers the services talk via service names (`db`, `rabbitmq`); on the host (where `poe migrate` runs) they're at `localhost`. Override via `DATABASE_URL` / `RABBITMQ_URL`.

### Start

```bash
uv run poe start
```

`poe start` is `docker compose up` in the foreground — web, orchestrator, worker, Postgres, and RabbitMQ all come up together. Open `http://localhost:8000`. Ctrl+C stops them. `uv run poe down` stops everything in the background-detached case. RabbitMQ's management UI is at `http://localhost:15672` (`guest`/`guest`).

Source code is bind-mounted into the containers, so editing files under `web/`, `runner/`, `db/`, or `messaging/` is picked up immediately by the web server's `--reload`. The orchestrator and worker don't auto-reload — restart them with `docker compose restart orchestrator worker` after editing their code. Only `pyproject.toml` / `uv.lock` changes require a `docker compose build`.

---

## Developing the App

### Local architecture

Everything runs in Docker Compose: `db` (Postgres), `rabbitmq`, `web`, `orchestrator`, and `worker`. The browser talks to the web; everything else talks via Postgres + RabbitMQ. Services find each other by service name (`db`, `rabbitmq`) over the compose network. Externally only the broker ports, the DB port, and `http://localhost:8000` are exposed.

#### 1. Uploading a bot

```mermaid
sequenceDiagram
    autonumber
    actor Browser
    participant Web
    participant DB as Postgres
    participant RMQ as RabbitMQ

    Browser->>Web: POST /submit (.py)
    Web->>Web: parse docstring,<br/>extract name + python version
    Web->>DB: INSERT bot row + source bytes
    Web->>RMQ: publish MatchJob per unplayed pair<br/>(matches.todo)
    Web-->>Browser: HTML "submitted successfully"
```

#### 2. Viewing the leaderboard or matches

The pages re-poll their data region every 2 seconds, so results appear without a manual refresh.

```mermaid
sequenceDiagram
    autonumber
    actor Browser
    participant Web
    participant DB as Postgres

    loop Every 2s
        Browser->>Web: GET /leaderboard (or /matches)
        Web->>DB: aggregate query<br/>(latest version per family +<br/>current + lifetime stats)
        DB-->>Web: rows
        Web-->>Browser: HTML fragment<br/>(live-poll swaps the table in place)
    end
```

#### 3. Running a match

One `MatchJob` on `matches.todo` produces one match. The orchestrator drives the game loop, RPC-ing each turn to the right per-Python-version worker queue and waiting on its reply queue.

```mermaid
sequenceDiagram
    autonumber
    participant RMQ as RabbitMQ
    participant Orch as Orchestrator
    participant DB as Postgres
    participant Worker as Turn worker (py3)

    RMQ->>Orch: deliver MatchJob from matches.todo
    Orch->>DB: get bots

    loop For each turn (up to 9)
        Orch->>RMQ: publish turn request<br/>(turn.py3.requests, with correlation_id)
        RMQ->>Worker: deliver
        Worker->>Worker: write tmpfile,<br/>run `python bot.py`<br/>(stdin = board, timeout)
        Worker->>RMQ: publish reply<br/>(orchestrator's reply queue)
        RMQ->>Orch: deliver reply
        Orch->>Orch: validate move,<br/>check winner / draw
    end

    Orch->>DB: INSERT match + moves
    Orch->>RMQ: ack matches.todo message
```

Web, orchestrator, and worker all run as compose services alongside Postgres and RabbitMQ — built from a single multi-stage `Dockerfile` with three targets (`web`, `orchestrator`, `worker`). The orchestrator is Python-version-agnostic; each turn worker is bound to one Python version (currently just `py3.13`). Adding more versions = adding more worker services consuming their own `turn.pyX.Y.requests` queue.

### Project layout

```text
tic-tac-toe-event/
├── web/            # FastAPI app (submission UI, leaderboard, matches)
├── runner/         # orchestrator.py (game loop) + turn_worker.py (bot subprocess) + engine.py (pure board logic)
├── db/             # SQLAlchemy models, async query helpers, bot source stored in `bots.source` BYTEA
├── messaging/      # Queue + RPC abstraction; RabbitMQ implementation
├── example_bots/   # Reference bots; `poe seed-examples` loads these into the DB
├── alembic/        # Migration scripts (versions/)
└── tests/          # Test suite
```

Stack: FastAPI · SQLAlchemy 2.x (async, `asyncpg`) on Postgres · RabbitMQ (`aio-pika`) for match queueing + per-turn RPC · Alembic for migrations · Docker Compose for the entire stack (Postgres, RabbitMQ, web, orchestrator, worker) — web/orchestrator/worker built from a single multi-stage `Dockerfile`. Tests use a recording in-memory queue and an isolated `ttt_test` database on the running Postgres.

### Common tasks

| Command | Description |
|---|---|
| `uv run poe start` | Whole stack in the foreground via `docker compose up` (Ctrl+C stops it) |
| `uv run poe up` | Whole stack detached (`docker compose up -d`) |
| `uv run poe down` | Stop every compose service |
| `uv run poe dev` | Host-only web server with auto-reload (bring up `db` and `rabbitmq` yourself with `docker compose up -d db rabbitmq` first) |
| `uv run poe migrate` | Apply pending Alembic migrations (run from the host against `localhost:5432`) |
| `uv run poe reset-db` | Drop & recreate the DB **and** purge every RabbitMQ queue (so no stale match jobs linger from the previous DB) |
| `uv run poe seed-examples` | Wipe bots/matches/moves, insert every file under `example_bots/` as a bot (multiple files sharing a `name:` auto-version), then enqueue every bot pair on `matches.todo` |
| `uv run poe test` | Run the test suite with coverage |
| `uv run poe lint` | Check code with ruff |
| `uv run poe lint-md` | Lint Markdown files with pymarkdown |
| `uv run poe format` | Auto-format with ruff |
| `uv run poe typecheck` | Type-check with ty |
| `uv run poe check` | Run lint + lint-md + typecheck + test in sequence |

### Changing the schema

Models live in `db/models/` as SQLAlchemy ORM classes (one file per model). To change the schema:

```bash
uv run alembic revision --autogenerate -m "describe the change"
# review the generated file under alembic/versions/, edit if needed
uv run poe migrate
```

### How matches run

A match starts as a message on `matches.todo`. The web app publishes one per ordered pair `(X, O)` — including each bot's self-pair, which catches strategies that misbehave when mirrored — whenever a bot is uploaded. `seed-examples` publishes the full N×N set at once.

The **orchestrator** consumes those messages and drives the game loop:

1. Fetch both bots' source bytes from Postgres.
2. For each turn (X then O, alternating), publish an RPC request on `turn.pyX.Y.requests` carrying `{symbol, board, source}` and a correlation id; wait for the reply on the orchestrator's exclusive reply queue.
3. Validate the worker's response: parseable 3×3 board, exactly one new piece, correct symbol, nothing overwritten.
4. Check for a win (three in a row / column / diagonal) or a cat game.
5. Swap symbols and repeat until the game ends.

The **turn worker** (one per supported Python version) receives each turn, writes the source to a tmpfile, runs `python <tmpfile>` as a subprocess with the symbol + board piped to stdin (subject to a per-move timeout), and publishes whatever the bot printed back to the orchestrator.

Any validation failure, exception, or timeout is an immediate forfeit for whichever bot was on the move. The orchestrator persists every move + the final outcome so matches can be replayed from the UI.

---

*Organized by the Iowa Python Users Group — [pyowa.org](https://pyowa.org)*
