#!/usr/bin/env python3
"""
Register every bot under example_bots/ as a v1 bot in the database and copy
the source files into bots/ under their sanitized names — the same path the
web upload flow would produce.

Clears existing bots/matches/moves first so the result is a known starting
state. Does not create any matches; the runner will pick that up.

Run with: uv run python scripts/seed_example_bots.py
"""

import secrets
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import DB_PATH
from web.main import extract_bot_name, extract_python_version, safe_filename_base

ROOT = Path(__file__).parent.parent
EXAMPLE_BOTS_DIR = ROOT / "example_bots"
BOTS_DIR = ROOT / "bots"


def main() -> None:
    BOTS_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    print("Clearing existing bots, matches, and moves...")
    conn.execute("DELETE FROM moves")
    conn.execute("DELETE FROM matches")
    conn.execute("DELETE FROM bots")
    conn.commit()

    sources = sorted(EXAMPLE_BOTS_DIR.glob("*.py"))
    if not sources:
        print(f"No .py files found under {EXAMPLE_BOTS_DIR}")
        return

    inserted = 0
    for src in sources:
        source = src.read_text()
        bot_name = extract_bot_name(source)
        if not bot_name:
            print(f"  Skipping {src.name}: no 'name:' field in docstring")
            continue

        python_version = extract_python_version(source) or "3"
        dest_name = f"{safe_filename_base(bot_name)}.py"
        dest = BOTS_DIR / dest_name
        shutil.copyfile(src, dest)

        conn.execute(
            """INSERT INTO bots
               (base_name, versioned_name, version,
                owner_token, file_path, python_version)
               VALUES (?, ?, 1, ?, ?, ?)""",
            (bot_name, bot_name, secrets.token_hex(32), str(dest), python_version),
        )
        inserted += 1
        print(f"  {bot_name!r}  ->  {dest.relative_to(ROOT)}")

    conn.commit()
    conn.close()
    print(f"\nInserted {inserted} bots. Start the server and runner to begin matches.")


if __name__ == "__main__":
    main()
