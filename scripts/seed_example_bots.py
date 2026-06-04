#!/usr/bin/env python3
"""
Register every bot under example_bots/ as a v1 bot in the database (source
bytes stored on the row), then enqueue every bot pair to `matches.todo` so
the orchestrator has work to do. Clears existing bots/matches/moves first.

Run with: uv run poe seed-examples  (or `python -m scripts.seed_example_bots`)
"""

import asyncio
import secrets
from pathlib import Path

from sqlalchemy import Engine, text

from db.database import create_sync_engine
from messaging import MatchJob, get_queue, pick_python_version
from web.utils import extract_bot_name, extract_python_version, versioned_name

ROOT = Path(__file__).parent.parent
EXAMPLE_BOTS_DIR = ROOT / "example_bots"


async def enqueue_all_pairs(engine: Engine) -> int:
    """Enqueue one MatchJob per ordered pair (including self-pairs) so the
    orchestrator gets the full Cartesian product to work through."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, python_version FROM bots ORDER BY id")
        ).fetchall()

    queue = get_queue()
    count = 0
    for x_id, x_py in rows:
        for o_id, o_py in rows:
            py = pick_python_version(x_py, o_py)
            await queue.enqueue_match(MatchJob(x_id, o_id, py))
            count += 1
    return count


def main() -> None:
    engine = create_sync_engine()

    with engine.begin() as conn:
        print("Clearing existing bots, matches, and moves...")
        conn.execute(text("DELETE FROM moves"))
        conn.execute(text("DELETE FROM matches"))
        conn.execute(text("DELETE FROM bots"))

    sources = sorted(EXAMPLE_BOTS_DIR.glob("*.py"))
    if not sources:
        print(f"No .py files found under {EXAMPLE_BOTS_DIR}")
        return

    inserted = 0
    with engine.begin() as conn:
        for src in sources:
            source_bytes = src.read_bytes()
            source_text = source_bytes.decode("utf-8", errors="replace")
            bot_name = extract_bot_name(source_text)
            if not bot_name:
                print(f"  Skipping {src.name}: no 'name:' field in docstring")
                continue

            python_version = extract_python_version(source_text) or "3"

            # Multiple files can share a `name:` (e.g. perfect_bot_v1.py and
            # perfect_bot_v2.py both say "Perfect Bot"). Auto-version them
            # the same way the web upload flow does.
            current_max = conn.execute(
                text("SELECT MAX(version) FROM bots WHERE base_name = :n"),
                {"n": bot_name},
            ).scalar() or 0
            version = current_max + 1
            v_name = versioned_name(bot_name, version)

            conn.execute(
                text(
                    """INSERT INTO bots
                       (base_name, versioned_name, version,
                        owner_token, python_version, source)
                       VALUES (:b, :v, :ver, :t, :py, :src)"""
                ),
                {
                    "b": bot_name,
                    "v": v_name,
                    "ver": version,
                    "t": secrets.token_hex(32),
                    "py": python_version,
                    "src": source_bytes,
                },
            )
            inserted += 1
            print(f"  {src.name:30s} -> {v_name}")

    queued = asyncio.run(enqueue_all_pairs(engine))
    print(f"\nInserted {inserted} bots, enqueued {queued} match jobs to matches.todo.")
    print("Run `poe start` (or just the orchestrator + worker) to play them out.")


if __name__ == "__main__":
    main()
