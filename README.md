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

Valid `python:` values: a major version (`3`) or `major.minor` (`3.11`, `3.12`, `3.13`).

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

uv run poe db-up        # start Postgres + RabbitMQ in containers
uv run poe migrate      # create the schema
```

Defaults: Postgres at `postgresql+asyncpg://ttt:ttt@localhost:5432/ttt`, RabbitMQ at `amqp://guest:guest@localhost:5672/`. Override via `DATABASE_URL` / `RABBITMQ_URL`.

### Start

```bash
uv run poe start
```

This launches the web server, the match orchestrator, and a py3 turn-worker — all in the foreground. Open `http://localhost:8000`. Ctrl+C stops them. `uv run poe db-down` stops Postgres + RabbitMQ. RabbitMQ's management UI is at `http://localhost:15672` (`guest`/`guest`).

---

## Developing the App

### Local architecture

```mermaid
flowchart LR
    Browser([Browser])

    subgraph host["Host processes (poe start)"]
        Web["Web<br/>FastAPI<br/>(web/main.py)"]
        Orch["Orchestrator<br/>(runner/orchestrator.py)"]
        Worker["Turn worker py3<br/>(runner/turn_worker.py)"]
    end

    subgraph compose["docker compose"]
        DB[("Postgres<br/>bots · matches · moves<br/>(bot source bytes live on the bots row — single source of truth)")]
        RMQ{{"RabbitMQ<br/>matches.todo · turn.py3.requests<br/>+ orchestrator reply queue"}}
    end

    Browser -- "upload .py" --> Web
    Browser -- "poll leaderboard / matches" --> Web
    Web -- "insert bot row + source bytes" --> DB
    Web -- "publish MatchJob per unplayed pair" --> RMQ
    Web -- "read leaderboard / matches" --> DB

    RMQ -- "deliver matches.todo" --> Orch
    Orch -- "fetch X &amp; O source by id" --> DB
    Orch -- "publish turn request<br/>(turn.py3.requests)" --> RMQ
    RMQ -- "deliver turn request" --> Worker
    Worker -- "publish reply<br/>(orchestrator's reply queue)" --> RMQ
    RMQ -- "deliver reply" --> Orch
    Orch -- "record match + moves" --> DB
```

The web app and the runner processes all run natively on the host today; only Postgres and RabbitMQ live in containers. The orchestrator is Python-version-agnostic; each turn worker is bound to one Python version (currently just `py3`). Adding more versions = adding more workers consuming their own `turn.pyX.Y.requests` queue.

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

Stack: FastAPI · SQLAlchemy 2.x (async, `asyncpg`) on Postgres · RabbitMQ (`aio-pika`) for match queueing + per-turn RPC · Alembic for migrations · Docker Compose for Postgres + RabbitMQ. Tests use a recording in-memory queue and an isolated `ttt_test` database on the running Postgres.

### Common tasks

| Command | Description |
|---|---|
| `uv run poe start` | Web + orchestrator + py3 worker, all in foreground (Ctrl+C stops them) |
| `uv run poe dev` | Web server only, with auto-reload |
| `uv run poe orchestrator` | Match orchestrator only (consumes `matches.todo`, drives RPC game loop) |
| `uv run poe worker` | Single turn-worker on `turn.pyX.requests` (`WORKER_PYTHON_VERSION` env var, default `3`) |
| `uv run poe db-up` | Start the Postgres + RabbitMQ containers (`docker compose up -d`) |
| `uv run poe db-down` | Stop the compose services |
| `uv run poe migrate` | Apply pending Alembic migrations |
| `uv run poe reset-db` | Drop & recreate the DB **and** purge every RabbitMQ queue (so no stale match jobs linger from the previous DB) |
| `uv run poe seed-examples` | Wipe bots/matches/moves, insert every file under `example_bots/` as a bot (multiple files sharing a `name:` auto-version), then enqueue every bot pair on `matches.todo` |
| `uv run poe test` | Run the test suite with coverage |
| `uv run poe lint` | Check code with ruff |
| `uv run poe lint-md` | Lint Markdown files with pymarkdown |
| `uv run poe format` | Auto-format with ruff |
| `uv run poe typecheck` | Type-check with ty |
| `uv run poe check` | Run lint + typecheck + test in sequence |

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
