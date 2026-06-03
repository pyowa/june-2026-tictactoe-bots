import signal
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import DB_PATH
from runner.engine import MatchResult, play_match

POLL_INTERVAL = 5
BOT_TIMEOUT = 10  # seconds per move; covers Docker startup (~1-2s) + bot execution


def unique_python_versions(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT DISTINCT python_version FROM bots").fetchall()
    conn.close()
    return {row[0] for row in rows}


def pull_images(db_path: str) -> None:
    versions = unique_python_versions(db_path)
    for version in sorted(versions):
        image = f"python:{version}"
        print(f"Pulling {image}...")
        result = subprocess.run(
            ["docker", "pull", image], capture_output=False
        )
        if result.returncode != 0:
            print(f"  Warning: failed to pull {image}")


def find_unplayed_pairs(db_path: str) -> list[tuple[int, str, str, int, str, str]]:
    conn = sqlite3.connect(db_path)
    rows: list[tuple[int, str, str, int, str, str]] = conn.execute(
        """
        SELECT a.id, a.file_path, a.python_version,
               b.id, b.file_path, b.python_version
        FROM bots a
        JOIN bots b
        WHERE NOT EXISTS (
            SELECT 1 FROM matches m
            WHERE m.bot_x_id = a.id AND m.bot_o_id = b.id
        )
        """
    ).fetchall()
    conn.close()
    return rows


def record_match(
    db_path: str,
    bot_x_id: int,
    bot_o_id: int,
    result: MatchResult,
) -> None:
    if result.result in ("x_wins", "o_forfeit"):
        winner_id: int | None = bot_x_id
    elif result.result in ("o_wins", "x_forfeit"):
        winner_id = bot_o_id
    else:
        winner_id = None

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO matches (bot_x_id, bot_o_id, winner_id, result) VALUES (?,?,?,?)",
        (bot_x_id, bot_o_id, winner_id, result.result),
    )
    match_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for move in result.moves:
        bot_id = bot_x_id if move.player == "x" else bot_o_id
        conn.execute(
            "INSERT INTO moves (match_id, move_number, bot_id, board_state, error)"
            " VALUES (?,?,?,?,?)",
            (match_id, move.move_number, bot_id, move.board, move.error),
        )
    conn.commit()
    conn.close()


def run(db_path: str = DB_PATH, poll_interval: int = POLL_INTERVAL) -> None:
    shutdown = False

    def _handle_signal(sig: int, frame: types.FrameType | None) -> None:
        nonlocal shutdown
        shutdown = True
        print("\nShutting down after current match...")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print("Runner started. Press Ctrl+C to stop.")
    pull_images(db_path)

    pulled_versions: set[str] = set()

    while not shutdown:
        pairs = find_unplayed_pairs(db_path)
        if not pairs:
            time.sleep(poll_interval)
            continue
        new_versions = unique_python_versions(db_path) - pulled_versions
        if new_versions:
            pull_images(db_path)
            pulled_versions = unique_python_versions(db_path)
        for x_id, x_path, x_py, o_id, o_path, o_py in pairs:
            if shutdown:
                break
            print(f"Running: bot {x_id} (X, py{x_py}) vs bot {o_id} (O, py{o_py})")
            result = play_match(x_path, o_path, x_py, o_py, BOT_TIMEOUT)
            record_match(db_path, x_id, o_id, result)
            print(f"  Result: {result.result}")

    print("Runner stopped.")


if __name__ == "__main__":
    run()
