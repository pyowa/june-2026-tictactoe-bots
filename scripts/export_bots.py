#!/usr/bin/env python3
"""Export every bot's source bytes to .py files on disk, plus a
`leaderboard.md` snapshot of the final standings.

Useful after an event to archive participant submissions to a local
directory. Run with:

    uv run python -m scripts.export_bots                 # writes to ./extracted_bots/
    uv run python -m scripts.export_bots ./some-dir/     # writes to ./some-dir/

Connects via the standard `DATABASE_URL` env var (default
`postgresql+asyncpg://ttt:ttt@localhost:5432/ttt`, which is what the kind
cluster exposes on the host)."""

import asyncio
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import undefer

from db.session import get_session
from entities.bot.model import Bot
from entities.bot.repository import BotRepository
from entities.match.model import Match  # noqa: F401 — registers Match mapper
from entities.move.model import Move  # noqa: F401 — registers Move mapper

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
DEFAULT_OUT_DIR = Path("extracted_bots")


def safe_filename(versioned_name: str) -> str:
    """Sanitize `versioned_name` for filesystem use, appending `.py`.

    Replaces any run of non-alphanumeric/dash/underscore characters with a
    single underscore. Names like `AlphaV2` pass through unchanged."""
    return _UNSAFE.sub("_", versioned_name) + ".py"


def _md_escape(s: str) -> str:
    """Escape `|` (and trailing/leading whitespace effects) for markdown
    table cells. `|` is the only character with cell-breaking semantics."""
    return s.replace("|", "\\|")


def render_leaderboard_md(rows: Sequence[Any]) -> str:
    """Render the leaderboard rows as a GitHub-flavored markdown table.

    Column set mirrors `web/templates/leaderboard.html`: rank, bot name,
    clean wins, forfeit wins, draws, losses, lifetime W-L."""
    if not rows:
        return "# Leaderboard\n\nNo bots submitted yet.\n"

    lines = [
        "# Leaderboard",
        "",
        "| # | Bot | Clean Wins | Forfeit Wins | Draws | Losses | Lifetime |",
        "|---|-----|------------|--------------|-------|--------|----------|",
    ]
    for i, row in enumerate(rows, start=1):
        lines.append(
            f"| {i} | {_md_escape(row.versioned_name)} | "
            f"{row.clean_wins} | {row.forfeit_wins} | "
            f"{row.draws} | {row.losses} | "
            f"{row.lifetime_wins}-{row.lifetime_losses} |"
        )
    return "\n".join(lines) + "\n"


async def export_leaderboard(out_dir: Path) -> Path:
    """Write the current leaderboard as a markdown table to
    `<out_dir>/leaderboard.md`. Returns the path written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    async with get_session() as session:
        bots = BotRepository(session)
        rows = await bots.leaderboard()
    path = out_dir / "leaderboard.md"
    path.write_text(render_leaderboard_md(rows))
    return path


async def export_all(out_dir: Path) -> int:
    """Write every bot's `source` bytes to `<out_dir>/<safe_name>.py`.

    Returns the number of files written. Empty-source rows are skipped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    async with get_session() as session:
        # `Bot.source` is deferred by default — opt-in eager load via
        # `undefer` so we can read each row's bytes inside this loop.
        result = await session.scalars(select(Bot).options(undefer(Bot.source)))
        for bot in result.all():
            if not bot.source:
                continue
            (out_dir / safe_filename(bot.versioned_name)).write_bytes(bot.source)
            written += 1
    return written


def main() -> None:  # pragma: no cover -- thin CLI wrapper
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT_DIR

    async def _run() -> tuple[int, Path]:
        bot_count = await export_all(out_dir)
        md_path = await export_leaderboard(out_dir)
        return bot_count, md_path

    count, md = asyncio.run(_run())
    print(f"Exported {count} bot(s) to {out_dir}/")
    print(f"Wrote leaderboard to {md}")


if __name__ == "__main__":  # pragma: no cover
    main()
