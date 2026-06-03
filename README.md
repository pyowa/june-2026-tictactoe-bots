# Pyowa Tic-Tac-Toe Bot Competition

A web platform for the Iowa Python Users Group (Pyowa) bot battle event. Participants submit Python bots that compete in automated tic-tac-toe matches, with results tracked on a live leaderboard.

---

## Writing a Bot

This is everything you need to participate. Read this section before anything else.

### 1. Start with the required docstring

Your bot must be a `.py` file whose very first thing is a docstring containing a `name:` field. This is how your bot appears on the leaderboard.

```python
"""
name: My Awesome Bot
"""
```

You can also specify which Python version to run your bot with. If you leave it out, the latest Python 3 is used.

```python
"""
name: My Awesome Bot
python: 3.11
"""
```

Valid values: a major version (`3`) or a major.minor version (`3.11`, `3.12`, `3.13`, etc.).

### 2. Read from stdin, write to stdout

Your bot is called once per move. It receives the current board state on stdin and must print the updated board to stdout.

**Input format:**
```
X
X|.|.
.|O|.
.|.|.
```

The first line is the symbol you are playing (`X` or `O`). The next three lines are the board — pipe-delimited, one row per line. Cells are `X`, `O`, or `.` (empty).

**Output format:**
```
X|.|.
.|O|.
.|X|.
```

Print the same board with exactly one new piece placed — your symbol in an empty cell.

### 3. Rules and forfeits

Your bot forfeits the game immediately if it:

- Produces no output
- Outputs something that isn't a valid 3×3 board
- Places more than one piece, or places in an already-occupied cell
- Places the wrong symbol (e.g. places `O` when you are `X`)
- Raises an unhandled exception
- Exceeds the per-move time limit

Forfeits are recorded separately from clean wins on the leaderboard so the result is never misleading.

### 4. Example bot

```python
"""
name: Top-Left Bot
"""
import sys

data = sys.stdin.read().strip().splitlines()
symbol = data[0]          # 'X' or 'O'
board = [row.split('|') for row in data[1:]]

for r in range(3):
    for c in range(3):
        if board[r][c] == '.':
            board[r][c] = symbol
            print('\n'.join('|'.join(row) for row in board))
            sys.exit(0)
```

### 5. Submit your bot

Go to the web UI at `http://localhost:8000`, upload your `.py` file, and your bot will be entered into the competition. The first time you submit a name you own it — the site sets a cookie so you can update your bot later. Re-uploading the same name increments the version automatically (`MyBot` → `MyBotV2` → `MyBotV3`). All versions compete independently.

---

## Overview

Participants write a Python script implementing a bot that plays tic-tac-toe. Submitted bots are run against each other in isolated Docker containers to ensure fair, sandboxed execution. Match results are recorded in a local database and displayed on a leaderboard.

## Features

- **Bot submission** — upload a Python script through the web UI
- **Automated matchmaking** — every bot plays every other bot as both X and O, and also plays a mirror match against itself (X vs. O run from the same script)
- **Sandboxed execution** — each match runs in an isolated Docker container with resource limits
- **Leaderboard** — live standings with separate clean wins and forfeit win columns
- **Match history** — view results, move logs, and game replays for any match

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Web Application                  │
│  (submission form · leaderboard · match history)    │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│                  Match Runner                       │
│  Pulls two bots · spins up Docker container ·       │
│  executes game · captures result                    │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│                Local Database                       │
│  bots · matches · moves · standings                 │
└─────────────────────────────────────────────────────┘
```

## Runner

The runner orchestrates a single match between two bots, alternating turns until the game ends.

**Pairing:** the runner schedules every ordered pair `(X, O)` from the set of submitted bots, including the self-pair `(Bot, Bot)`. Each self-match runs as two independent subprocesses from the same source file — the bot plays both sides, with no shared state between turns. This is useful for catching bugs that only surface when a bot faces its own strategy.

**Turn loop:**

1. Send the current board state to the active bot via stdin
2. Read the bot's stdout response (subject to the per-move time limit)
3. Validate the response:
   - Output is parseable as a 3×3 pipe-delimited board
   - Exactly one new piece was placed compared to the input board
   - The piece placed is the correct symbol for that turn
   - No existing pieces were moved or overwritten
4. Check for a win or draw:
   - **Win** — three matching symbols in a row, column, or diagonal. The bot that just moved is recorded as the winner; the other is the loser.
   - **Cat game** — all nine cells are filled with no winner. Both bots are recorded as `cat` for the match.
5. If the game continues, swap to the other bot and repeat

Any validation failure at step 3, unhandled exception, or timeout is an immediate forfeit. The offending bot is recorded as having forfeited; the opponent is awarded the win.

**Match logging:** For every match the runner records to the database:
- Which bot is playing X and which is playing O
- The board state after each move, in order
- The outcome (win/draw/forfeit) and which bot caused a forfeit if applicable
- Any errors or invalid output produced by either bot

## Bot Ownership and Versioning

When you successfully upload a bot, the site sets a cookie in your browser recording your ownership of that bot name. This is how subsequent uploads are handled:

- **Same name, you have the cookie** — your new submission is accepted and registered as the next version. `MyBot` becomes `MyBotV2`, then `MyBotV3`, and so on. All versions are retained and can compete independently.
- **Same name, you don't have the cookie** — the upload is rejected with an error indicating the name is already claimed. Choose a different name.

No accounts or passwords are involved. Clearing your cookies means losing the ability to update your bot under its original name, so hold onto that browser session for the duration of the event.

The site will display a warning banner if cookies are disabled, since submission and versioning will not work without them.

## Tech Stack

| Layer | Choice |
|---|---|
| Web framework | FastAPI |
| Database | SQLite |
| Bot execution | Docker (subprocess stdin/stdout) |
| Language | Python 3.11+ |

## Getting Started

**Prerequisites:** [uv](https://docs.astral.sh/uv/), Python 3.11+, Docker

```bash
git clone <repo>
cd tic-tac-toe-event
uv sync --group dev
```

### Common tasks

| Command | Description |
|---|---|
| `uv run poe start` | Start the web server and the match runner together (Ctrl+C stops both) |
| `uv run poe dev` | Start the web server with auto-reload |
| `uv run poe runner` | Start the match runner process |
| `uv run poe test` | Run the test suite with coverage |
| `uv run poe lint` | Check code with ruff |
| `uv run poe format` | Auto-format code with ruff |
| `uv run poe typecheck` | Type-check with ty |
| `uv run poe check` | Run lint, typecheck, and test in sequence |
| `uv run poe seed` | Seed the database with fake bots and matches |

The app will be available at `http://localhost:8000` after running `start` or `dev`. The SQLite database (`ttt.db`) and the `bots/` directory are created automatically on first run.

## Project Structure

```
tic-tac-toe-event/
├── web/            # Web application (submission UI, leaderboard)
├── runner/         # Match execution engine and Docker integration
├── db/             # Database schema and migrations
├── bots/           # Submitted bot scripts (stored on upload)
└── tests/          # Test suite
```

## Event Rules

Details about submission deadlines, match format, scoring, and prize structure will be published separately for event participants.

---

*Organized by the Iowa Python Users Group — [pyowa.org](https://pyowa.org)*
