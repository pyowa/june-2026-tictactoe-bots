#!/usr/bin/env python3
"""
Register every bot under example_bots/ as a v1 bot in the database (source
bytes stored on the row), then publish one BuildPodMessage per bot to
`matches.build` so pod_builder can create pods and match_scheduler can
schedule matches. Clears existing bots/matches/moves first.

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
from messaging.contracts import BuildPodMessage
from web.utils import (
    _python_version_from_runtime_key,
    extract_bot_name,
    extract_runtime_key,
    versioned_name,
)

ROOT = Path(__file__).parent.parent
EXAMPLE_BOTS_DIR = ROOT / "example_bots"


# TODO smell
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

            from web.runtimes import DEFAULT_RUNTIME_KEY

            rk = extract_runtime_key(source_text) or DEFAULT_RUNTIME_KEY
            python_version = _python_version_from_runtime_key(rk)

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
                runtime_key=rk,
                source=source_bytes,
            )
            inserted += 1
            print(f"  {src.name:30s} -> {v_name}")

    queue = make_queue()
    async with get_session() as session:
        all_bots = await BotRepository(session).all()

    count = 0
    for bot in all_bots:
        await queue.enqueue_build_pod(
            BuildPodMessage(bot_id=bot.id, runtime_key=bot.runtime_key)
        )
        count += 1

    print(
        f"\nInserted {inserted} bots, enqueued {count} build-pod jobs to matches.build."
    )


if __name__ == "__main__":
    asyncio.run(main())
