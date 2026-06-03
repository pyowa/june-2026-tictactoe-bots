#!/usr/bin/env python3
"""
Drop and recreate the database by deleting the SQLite file and re-running
all Alembic migrations from scratch.

Does not touch the `bots/` directory — remove those manually if you want a
fully clean slate.

Run with: uv run python scripts/reset_db.py
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import DB_PATH


def main() -> None:
    if os.path.exists(DB_PATH):
        print(f"Deleting {DB_PATH}...")
        os.remove(DB_PATH)
    else:
        print(f"{DB_PATH} does not exist; skipping delete.")

    print("Running migrations...")
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    print("Done.")


if __name__ == "__main__":
    main()
