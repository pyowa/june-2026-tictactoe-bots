# Pyowa Tic-Tac-Toe Bot Competition

A web platform for the Iowa Python Users Group (Pyowa) bot battle event. Participants submit Python bots that compete in automated tic-tac-toe matches, with results tracked on a live leaderboard.

---

## Overview

Participants write a Python script implementing a bot that plays tic-tac-toe. Submitted bots are run against each other in isolated Docker containers to ensure fair, sandboxed execution. Match results are recorded in a local database and displayed on a leaderboard.

## Features

- **Bot submission** — upload a Python script through the web UI
- **Automated matchmaking** — bots are paired and scheduled for matches
- **Sandboxed execution** — each match runs in an isolated Docker container with resource limits
- **Leaderboard** — live standings based on win/loss/draw record
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

**First move:** The runner sends `X`, a blank line, then an empty board (`.|.|.` × 3) to bot X. Bot X plays first and must place an `X`.

Any validation failure at step 3, unhandled exception, or timeout is an immediate forfeit. The offending bot is recorded as having forfeited; the opponent is awarded the win. Forfeits are flagged distinctly from clean wins on the leaderboard and in match history so the result is not misleading.

**Match logging:** For every match the runner records to the database:
- Which bot is playing X and which is playing O
- The board state after each move, in order
- The outcome (win/draw/forfeit) and which bot caused a forfeit if applicable
- Any errors or invalid output produced by either bot

## Bot Requirements

### Required docstring

Every submitted bot must begin with a module-level docstring containing a `name:` field. This is how your bot will appear on the leaderboard.

```python
"""
name: My Awesome Bot
"""
```

The upload will be rejected if the docstring is missing or does not contain a `name:` line.

### Bot ownership and versioning

When you successfully upload a bot, the site sets a cookie in your browser recording your ownership of that bot name. This is how subsequent uploads are handled:

- **Same name, you have the cookie** — your new submission is accepted and registered as the next version. `MyBot` becomes `MyBotV2`, then `MyBotV3`, and so on. All versions are retained and can compete independently.
- **Same name, you don't have the cookie** — the upload is rejected with an error indicating the name is already claimed. Choose a different name.

No accounts or passwords are involved. Clearing your cookies means losing the ability to update your bot under its original name, so hold onto that browser session for the duration of the event.

The site will display a warning banner if cookies are disabled, since submission and versioning will not work without them.

### Interface

Bots communicate with the match runner over **stdin/stdout** and are fully stateless — each invocation is one move.

**Input** (written to the bot's stdin):
```
X
X|.|.
.|O|.
.|.|.
```
The first line is the symbol your bot is playing (`X` or `O`), followed by the 3×3 board — pipe-delimited, one row per line. Cells are `X`, `O`, or `.` (empty).

**Output** (bot writes to stdout):
```
X|.|.
.|O|.
.|X|.
```
The same board format with exactly one new piece placed.

Bots that produce invalid output, overwrite an existing piece, place the wrong symbol, raise an unhandled exception, or exceed the per-move time limit forfeit that game.

### Example bot

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

## Cookie Ownership Model

On a successful bot upload, the server generates a unique ownership token and:

1. Stores it in the database against that bot's base name (e.g. `MyBot`)
2. Sets it in the browser as a cookie in a map of `bot_name → token`

On a subsequent upload with the same name, the server reads the cookie, looks up the stored token for that name, and compares them. A match allows the versioned upload; a mismatch or missing cookie rejects it.

Because a participant may own multiple bots, the cookie holds a JSON map rather than a single token:

```json
{ "MyBot": "a3f9...", "AnotherBot": "7c2e..." }
```

The token itself is a random secret generated server-side at upload time and never reused across bot names.

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
| `uv run poe dev` | Start the web server with auto-reload |
| `uv run poe test` | Run the test suite with coverage |
| `uv run poe lint` | Check code with ruff |
| `uv run poe format` | Auto-format code with ruff |
| `uv run poe typecheck` | Type-check with ty |
| `uv run poe check` | Run lint, typecheck, and test in sequence |
| `uv run poe seed` | Seed the database with fake bots and matches |

The app will be available at `http://localhost:8000` after running `dev`. The SQLite database (`ttt.db`) and the `bots/` directory are created automatically on first run.

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
