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

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from db.database import create_sync_engine
from db.models.bot import Bot
from db.models.match import Match
from db.models.move import Move
from messaging.client import make_queue
from messaging.queue import MatchJob, Queue
from messaging.routing import pick_python_version
from web.utils import extract_bot_name, extract_python_version, versioned_name

ROOT = Path(__file__).parent.parent
EXAMPLE_BOTS_DIR = ROOT / "example_bots"


async def enqueue_all_pairs(engine: Engine, queue: Queue) -> int:
    """Enqueue one MatchJob per ordered pair (including self-pairs) so the
    orchestrator gets the full Cartesian product to work through."""
    with Session(engine) as session:
        bots = session.scalars(select(Bot).order_by(Bot.id)).all()

    count = 0
    for x in bots:
        for o in bots:
            py = pick_python_version(x.python_version, o.python_version)
            await queue.enqueue_match(MatchJob(x.id, o.id, py))
            count += 1
    return count


def main() -> None:
    engine = create_sync_engine()

    with Session(engine) as session, session.begin():
        print("Clearing existing bots, matches, and moves...")
        session.execute(delete(Move))
        session.execute(delete(Match))
        session.execute(delete(Bot))

    sources = sorted(EXAMPLE_BOTS_DIR.glob("*.py"))
    if not sources:
        print(f"No .py files found under {EXAMPLE_BOTS_DIR}")
        return

    inserted = 0
    with Session(engine) as session, session.begin():
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
            current_max = session.execute(
                select(func.max(Bot.version)).where(Bot.base_name == bot_name)
            ).scalar() or 0
            version = current_max + 1
            v_name = versioned_name(bot_name, version)

            session.add(
                Bot(
                    base_name=bot_name,
                    versioned_name=v_name,
                    version=version,
                    owner_token=secrets.token_hex(32),
                    python_version=python_version,
                    source=source_bytes,
                )
            )
            inserted += 1
            print(f"  {src.name:30s} -> {v_name}")

    queue = make_queue()
    queued = asyncio.run(enqueue_all_pairs(engine, queue))
    print(f"\nInserted {inserted} bots, enqueued {queued} match jobs to matches.todo.")
    print(
        "Run `docker compose up -d` to start the orchestrator + workers "
        "and play them out."
    )


if __name__ == "__main__":
    main()
