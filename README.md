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
- Docker (used to sandbox each match)

### Setup

```bash
git clone <repo>
cd tic-tac-toe-event
uv sync --group dev
uv run poe migrate   # creates ttt.db
```

### Start

```bash
uv run poe start
```

This launches the web server and the match runner together. Open `http://localhost:8000`. Ctrl+C stops both.

---

## Developing the App

### Project layout

```text
tic-tac-toe-event/
├── web/            # FastAPI app (submission UI, leaderboard, matches)
├── runner/         # Match orchestration + Docker-sandboxed bot execution
├── db/             # SQLAlchemy models and async query helpers
├── alembic/        # Migration scripts (versions/)
├── bots/           # Submitted bot scripts (created on first upload)
└── tests/          # Test suite
```

Stack: FastAPI · SQLAlchemy 2.x (async) on SQLite · Alembic · Docker for sandboxing.

### Common tasks

| Command | Description |
|---|---|
| `uv run poe start` | Web server + match runner together (Ctrl+C stops both) |
| `uv run poe dev` | Web server only, with auto-reload |
| `uv run poe runner` | Match runner only |
| `uv run poe migrate` | Apply pending Alembic migrations |
| `uv run poe reset-db` | Drop and recreate the database (does not touch `bots/`) |
| `uv run poe seed` | Populate the DB with fake bots and matches |
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

A database that pre-dates Alembic (no `alembic_version` table) needs a one-time `uv run alembic stamp head` before further migrations.

### How matches run

The runner schedules every ordered pair `(X, O)` of submitted bots — including each bot's self-pair, which catches strategies that misbehave when mirrored. For each match:

1. Send the current board to the active bot via stdin
2. Read its stdout (subject to the per-move timeout)
3. Validate: parseable 3×3 board, one new piece, correct symbol, nothing overwritten
4. Check for a win (three in a row/column/diagonal) or a cat game
5. Swap symbols and repeat until the game ends

Any validation failure, exception, or timeout is an immediate forfeit. Both moves and outcomes are persisted so matches can be replayed from the UI.

---

*Organized by the Iowa Python Users Group — [pyowa.org](https://pyowa.org)*
