#!/usr/bin/env python3
"""
Populate the database with fake bots and matches for development / demo purposes.

Clears all existing bots, matches, and moves before inserting seed data.
Run with: uv run python scripts/seed.py
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import DB_PATH

# ---------------------------------------------------------------------------
# Board helpers
# ---------------------------------------------------------------------------


def r(a, b, c):
    return f"{a}|{b}|{c}"


def board(row0, row1, row2):
    return "\n".join([row0, row1, row2])


# Reusable board progressions
# Each entry is (board_state_after_move,)  — bot assignment done at match level.

X_WINS_TOP_ROW = [
    board(r("X", ".", "."), r(".", ".", "."), r(".", ".", ".")),
    board(r("X", ".", "."), r(".", "O", "."), r(".", ".", ".")),
    board(r("X", "X", "."), r(".", "O", "."), r(".", ".", ".")),
    board(r("X", "X", "."), r(".", "O", "."), r("O", ".", ".")),
    board(r("X", "X", "X"), r(".", "O", "."), r("O", ".", ".")),
]

X_WINS_DIAGONAL = [
    board(r("X", ".", "."), r(".", ".", "."), r(".", ".", ".")),
    board(r("X", "O", "."), r(".", ".", "."), r(".", ".", ".")),
    board(r("X", "O", "."), r(".", "X", "."), r(".", ".", ".")),
    board(r("X", "O", "."), r(".", "X", "."), r("O", ".", ".")),
    board(r("X", "O", "."), r(".", "X", "."), r("O", ".", "X")),
]

O_WINS_MIDDLE_COL = [
    board(r("X", ".", "."), r(".", ".", "."), r(".", ".", ".")),
    board(r("X", ".", "."), r(".", "O", "."), r(".", ".", ".")),
    board(r("X", ".", "."), r(".", "O", "."), r("X", ".", ".")),
    board(r("X", "O", "."), r(".", "O", "."), r("X", ".", ".")),
    board(r("X", "O", "."), r(".", "O", "."), r("X", ".", "X")),
    board(r("X", "O", "."), r(".", "O", "."), r("X", "O", "X")),
]

CAT_GAME = [
    board(r("X", ".", "."), r(".", ".", "."), r(".", ".", ".")),
    board(r("X", ".", "."), r(".", ".", "."), r("O", ".", ".")),
    board(r("X", ".", "."), r("X", ".", "."), r("O", ".", ".")),
    board(r("X", "O", "."), r("X", ".", "."), r("O", ".", ".")),
    board(r("X", "O", "."), r("X", "X", "."), r("O", ".", ".")),
    board(r("X", "O", "."), r("X", "X", "O"), r("O", ".", ".")),
    board(r("X", "O", "X"), r("X", "X", "O"), r("O", ".", ".")),
    board(r("X", "O", "X"), r("X", "X", "O"), r("O", ".", "O")),
    board(r("X", "O", "X"), r("X", "X", "O"), r("O", "X", "O")),
]

# For forfeit matches: X makes one valid move, then O crashes.
FORFEIT_AS_O_BOARDS = [
    board(r("X", ".", "."), r(".", ".", "."), r(".", ".", ".")),
]

# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------


def insert_match(
    conn,
    bot_x_id,
    bot_o_id,
    winner_id,
    result,
    played_at,
    boards,
    error_on=None,
    error_msg=None,
):
    """Insert a match and its moves. boards is a list of board states in order."""
    conn.execute(
        """INSERT INTO matches (bot_x_id, bot_o_id, winner_id, result, played_at)
           VALUES (?, ?, ?, ?, ?)""",
        (bot_x_id, bot_o_id, winner_id, result, played_at),
    )
    match_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Alternate moves: X plays odd moves (1,3,5,...), O plays even moves (2,4,6,...)
    for i, board_state in enumerate(boards, 1):
        bot_id = bot_x_id if i % 2 == 1 else bot_o_id
        error = error_msg if i == error_on else None
        conn.execute(
            """INSERT INTO moves (match_id, move_number, bot_id, board_state, error)
               VALUES (?, ?, ?, ?, ?)""",
            (match_id, i, bot_id, board_state, error),
        )

    return match_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("Clearing existing data...")
    conn.execute("DELETE FROM moves")
    conn.execute("DELETE FROM matches")
    conn.execute("DELETE FROM bots")
    conn.commit()

    print("Inserting bots...")
    bot_rows = [
        ("Minimax Master", "2024-01-01 09:00:00"),
        ("Corner Bot", "2024-01-01 09:05:00"),
        ("Random Bot", "2024-01-01 09:10:00"),
        ("Crash Bot", "2024-01-01 09:15:00"),
        ("First Cell Bot", "2024-01-01 09:20:00"),
    ]
    bots = {}
    for i, (name, submitted_at) in enumerate(bot_rows):
        conn.execute(
            """INSERT INTO bots
               (base_name, versioned_name, version,
                owner_token, file_path, submitted_at)
               VALUES (?, ?, 1, ?, ?, ?)""",
            (name, name, f"seed-token-{i}", f"bots/{name}.py", submitted_at),
        )
        bots[name] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    mm = bots["Minimax Master"]
    cb = bots["Corner Bot"]
    rb = bots["Random Bot"]
    cr = bots["Crash Bot"]
    fc = bots["First Cell Bot"]

    print("Inserting matches...")
    matches = [
        # (bot_x, bot_o, winner, result, played_at, boards, error_on, error_msg)
        # Minimax Master clean wins
        (mm, fc, mm, "x_wins", "2024-01-01 10:00:00", X_WINS_TOP_ROW, None, None),
        (mm, rb, mm, "x_wins", "2024-01-01 10:30:00", X_WINS_DIAGONAL, None, None),
        (cb, mm, mm, "o_wins", "2024-01-01 11:00:00", O_WINS_MIDDLE_COL, None, None),
        (rb, mm, mm, "o_wins", "2024-01-01 11:30:00", O_WINS_MIDDLE_COL, None, None),
        (fc, mm, mm, "o_wins", "2024-01-01 12:00:00", O_WINS_MIDDLE_COL, None, None),
        # Minimax Master forfeit win (Crash Bot collapses as O)
        (
            mm,
            cr,
            mm,
            "o_forfeit",
            "2024-01-01 12:30:00",
            FORFEIT_AS_O_BOARDS,
            2,
            "unhandled exception: ZeroDivisionError: division by zero",
        ),
        # Corner Bot wins
        (cb, fc, cb, "x_wins", "2024-01-01 13:00:00", X_WINS_TOP_ROW, None, None),
        (
            cb,
            cr,
            cb,
            "o_forfeit",
            "2024-01-01 13:30:00",
            FORFEIT_AS_O_BOARDS,
            2,
            "timed out after 5s",
        ),
        # Corner Bot forfeit win when Crash Bot is X
        (
            cr,
            cb,
            cb,
            "x_forfeit",
            "2024-01-01 14:00:00",
            [],
            None,
            None,
        ),  # Crash forfeits immediately — no moves
        # Draw between Corner Bot and Random Bot
        (cb, rb, None, "cat", "2024-01-01 14:30:00", CAT_GAME, None, None),
        # Random Bot wins
        (rb, fc, rb, "x_wins", "2024-01-01 15:00:00", X_WINS_DIAGONAL, None, None),
        # Random Bot forfeit win (Crash Bot collapses as X)
        (
            cr,
            rb,
            rb,
            "x_forfeit",
            "2024-01-01 15:30:00",
            [],
            None,
            None,
        ),  # Crash forfeits immediately — no moves
        # Crash Bot racking up forfeit losses
        (cr, mm, mm, "x_forfeit", "2024-01-01 16:00:00", [], None, None),
        (cr, fc, fc, "x_forfeit", "2024-01-01 16:30:00", [], None, None),
    ]

    for bx, bo, winner, result, played_at, boards, error_on, error_msg in matches:
        mid = insert_match(
            conn, bx, bo, winner, result, played_at, boards, error_on, error_msg
        )
        winner_name = next((n for n, i in bots.items() if i == winner), "nobody")
        print(
            f"  Match {mid:>2}: "
            f"{next(n for n, i in bots.items() if i == bx)} (X) vs "
            f"{next(n for n, i in bots.items() if i == bo)} (O) "
            f"-> {result} ({winner_name} wins)"
        )

    conn.commit()
    conn.close()

    print("\nDone. Standings:")
    conn2 = sqlite3.connect(DB_PATH)
    conn2.row_factory = sqlite3.Row
    rows = conn2.execute("""
        SELECT
            b.versioned_name,
            COUNT(CASE WHEN m.winner_id = b.id
                       AND m.result IN ('x_wins','o_wins') THEN 1 END) AS clean_wins,
            COUNT(CASE WHEN m.winner_id = b.id
                       AND m.result IN ('x_forfeit','o_forfeit')
                       THEN 1 END) AS forfeit_wins,
            COUNT(CASE WHEN (m.bot_x_id=b.id OR m.bot_o_id=b.id)
                       AND m.result='cat' THEN 1 END) AS draws,
            COUNT(CASE WHEN (m.bot_x_id=b.id OR m.bot_o_id=b.id)
                       AND m.result!='cat'
                       AND m.winner_id!=b.id THEN 1 END) AS losses
        FROM bots b
        LEFT JOIN matches m ON (m.bot_x_id=b.id OR m.bot_o_id=b.id)
        GROUP BY b.id
        ORDER BY (clean_wins+forfeit_wins) DESC, b.submitted_at ASC
    """).fetchall()
    print(f"  {'Bot':<20} {'CW':>4} {'FW':>4} {'D':>4} {'L':>4}")
    print(f"  {'-' * 20} {'-' * 4} {'-' * 4} {'-' * 4} {'-' * 4}")
    for row in rows:
        print(
            f"  {row['versioned_name']:<20} "
            f"{row['clean_wins']:>4} "
            f"{row['forfeit_wins']:>4} "
            f"{row['draws']:>4} "
            f"{row['losses']:>4}"
        )
    conn2.close()


if __name__ == "__main__":
    main()
