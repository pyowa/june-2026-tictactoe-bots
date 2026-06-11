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

from sqlalchemy import delete

from db.session import get_session
from entities.bot.model import Bot
from entities.bot.repository import BotRepository
from entities.match.model import Match
from entities.move.model import Move
from messaging.client import make_queue
from messaging.queue import MatchJob, Queue
from messaging.routing import pick_python_version
from web.utils import extract_bot_name, extract_python_version, versioned_name

ROOT = Path(__file__).parent.parent
EXAMPLE_BOTS_DIR = ROOT / "example_bots"


async def enqueue_all_pairs(bots: BotRepository, queue: Queue) -> int:
    """Enqueue one MatchJob per ordered pair (including self-pairs) so the
    orchestrator gets the full Cartesian product to work through."""
    all_bots = await bots.all()

    count = 0
    for x in all_bots:
        for o in all_bots:
            py = pick_python_version(x.python_version, o.python_version)
            await queue.enqueue_match(
                MatchJob(
                    bot_x_id=x.id,
                    bot_o_id=o.id,
                    python_version=py,
                    correlation_id=secrets.token_hex(16),
                )
            )
            count += 1
    return count


async def main() -> None:
    async with get_session() as session:
        print("Clearing existing bots, matches, and moves...")
        await session.execute(delete(Move))
        await session.execute(delete(Match))
        await session.execute(delete(Bot))
        await session.commit()

    sources = sorted(EXAMPLE_BOTS_DIR.glob("*.py"))
    if not sources:
        print(f"No .py files found under {EXAMPLE_BOTS_DIR}")
        return

    inserted = 0
    async with get_session() as session:
        bots = BotRepository(session)
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
            version = await bots.next_version(bot_name)
            v_name = versioned_name(bot_name, version)

            await bots.create(
                base_name=bot_name,
                versioned_name=v_name,
                version=version,
                owner_token=secrets.token_hex(32),
                python_version=python_version,
                source=source_bytes,
            )
            inserted += 1
            print(f"  {src.name:30s} -> {v_name}")

    queue = make_queue()
    async with get_session() as session:
        queued = await enqueue_all_pairs(BotRepository(session), queue)
    print(f"\nInserted {inserted} bots, enqueued {queued} match jobs to matches.todo.")
    print(
        "Run `docker compose up -d` to start the orchestrator + workers "
        "and play them out."
    )


if __name__ == "__main__":
    asyncio.run(main())
