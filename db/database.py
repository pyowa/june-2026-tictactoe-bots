import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, func, or_, select, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from db.models.bot import Bot
from db.models.match import Match
from db.models.move import Move
from runner.engine import MatchResult

DEFAULT_ASYNC_URL = "postgresql+asyncpg://ttt:ttt@localhost:5432/ttt"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_ASYNC_URL)


def sync_url(async_url: str) -> str:
    """Convert an async SQLAlchemy URL to its sync counterpart so tools like
    the runner and Alembic offline mode can reuse the same connection string."""
    return async_url.replace("+asyncpg", "+psycopg2")


_engine = create_async_engine(DATABASE_URL)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def reconfigure(url: str) -> None:
    """Rebind the async engine to a new URL (used by tests).

    Uses NullPool so each request gets a fresh connection — avoids asyncpg's
    'attached to a different loop' errors when tests spin up multiple
    TestClient instances (each with its own event loop)."""
    global _engine, _session_factory, DATABASE_URL
    DATABASE_URL = url
    _engine = create_async_engine(url, poolclass=NullPool)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def create_sync_engine() -> Engine:
    """A fresh sync engine bound to the current `DATABASE_URL` — used by the
    runner and seed scripts so they speak whichever dialect is configured."""
    return create_engine(sync_url(DATABASE_URL))


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session


async def get_owner_token(session: AsyncSession, base_name: str) -> str | None:
    result = await session.execute(
        select(Bot.owner_token).where(Bot.base_name == base_name).limit(1)
    )
    return result.scalar_one_or_none()


async def get_next_version(session: AsyncSession, base_name: str) -> int:
    result = await session.execute(
        select(func.max(Bot.version)).where(Bot.base_name == base_name)
    )
    current = result.scalar()
    return (current or 0) + 1


async def record_match(
    session: AsyncSession,
    bot_x_id: int,
    bot_o_id: int,
    result: MatchResult,
) -> None:
    """Persist a completed match (and its moves) using an async session."""
    if result.result in ("x_wins", "o_forfeit"):
        winner_id: int | None = bot_x_id
    elif result.result in ("o_wins", "x_forfeit"):
        winner_id = bot_o_id
    else:
        winner_id = None

    match = Match(
        bot_x_id=bot_x_id,
        bot_o_id=bot_o_id,
        winner_id=winner_id,
        result=result.result,
    )
    session.add(match)
    await session.flush()  # populates match.id

    for move in result.moves:
        bot_id = bot_x_id if move.player == "x" else bot_o_id
        session.add(
            Move(
                match_id=match.id,
                move_number=move.move_number,
                bot_id=bot_id,
                board_state=move.board,
                error=move.error,
            )
        )
    await session.commit()


async def insert_bot(
    session: AsyncSession,
    base_name: str,
    versioned_name: str,
    version: int,
    owner_token: str,
    python_version: str = "3",
    source: bytes | None = None,
) -> None:
    session.add(
        Bot(
            base_name=base_name,
            versioned_name=versioned_name,
            version=version,
            owner_token=owner_token,
            python_version=python_version,
            source=source,
        )
    )
    await session.commit()


async def list_bots(session: AsyncSession) -> list[Row[Any]]:
    result = await session.execute(
        select(Bot.versioned_name, Bot.submitted_at).order_by(Bot.submitted_at.desc())
    )
    return list(result.all())


_LEADERBOARD_SQL = text(
    """
    WITH latest_per_family AS (
        SELECT base_name, MAX(version) AS max_v
        FROM bots
        GROUP BY base_name
    ),
    latest_bot AS (
        SELECT b.id, b.base_name, b.versioned_name, b.submitted_at
        FROM bots b
        JOIN latest_per_family l
          ON l.base_name = b.base_name AND l.max_v = b.version
    ),
    stats AS (
        SELECT
            lb.base_name,
            lb.versioned_name,
            lb.submitted_at,
            -- Stats for the current (latest) version only.
            (SELECT COUNT(*) FROM matches m
             WHERE m.winner_id = lb.id
               AND m.result IN ('x_wins', 'o_wins')) AS clean_wins,
            (SELECT COUNT(*) FROM matches m
             WHERE m.winner_id = lb.id
               AND m.result IN ('x_forfeit', 'o_forfeit')) AS forfeit_wins,
            (SELECT COUNT(*) FROM matches m
             WHERE (m.bot_x_id = lb.id OR m.bot_o_id = lb.id)
               AND m.result = 'cat') AS draws,
            (SELECT COUNT(*) FROM matches m
             WHERE (m.bot_x_id = lb.id OR m.bot_o_id = lb.id)
               AND m.result != 'cat'
               AND (m.winner_id IS NULL OR m.winner_id != lb.id)) AS losses,
            -- Lifetime W/L across every version of this family, excluding
            -- intra-family matches so a family playing itself doesn't
            -- inflate both sides.
            (SELECT COUNT(*) FROM matches m
             JOIN bots bx ON bx.id = m.bot_x_id
             JOIN bots bo ON bo.id = m.bot_o_id
             JOIN bots bw ON bw.id = m.winner_id
             WHERE bw.base_name = lb.base_name
               AND (bx.base_name != lb.base_name
                    OR bo.base_name != lb.base_name)) AS lifetime_wins,
            (SELECT COUNT(*) FROM matches m
             JOIN bots bx ON bx.id = m.bot_x_id
             JOIN bots bo ON bo.id = m.bot_o_id
             WHERE m.result != 'cat'
               AND (bx.base_name = lb.base_name
                    OR bo.base_name = lb.base_name)
               AND (bx.base_name != lb.base_name
                    OR bo.base_name != lb.base_name)
               AND (m.winner_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1 FROM bots bw
                        WHERE bw.id = m.winner_id
                          AND bw.base_name = lb.base_name
                    ))) AS lifetime_losses
        FROM latest_bot lb
    )
    SELECT *
    FROM stats
    ORDER BY (clean_wins + forfeit_wins) DESC, submitted_at ASC
    """
)


async def get_leaderboard(session: AsyncSession) -> list[Row[Any]]:
    result = await session.execute(_LEADERBOARD_SQL)
    return list(result.all())


_BOT_FAMILY_SQL = text(
    """
    SELECT
        b.versioned_name,
        b.version,
        b.submitted_at,
        (SELECT COUNT(*) FROM matches m
         WHERE m.winner_id = b.id
           AND m.result IN ('x_wins', 'o_wins')) AS clean_wins,
        (SELECT COUNT(*) FROM matches m
         WHERE m.winner_id = b.id
           AND m.result IN ('x_forfeit', 'o_forfeit')) AS forfeit_wins,
        (SELECT COUNT(*) FROM matches m
         WHERE (m.bot_x_id = b.id OR m.bot_o_id = b.id)
           AND m.result = 'cat') AS draws,
        (SELECT COUNT(*) FROM matches m
         WHERE (m.bot_x_id = b.id OR m.bot_o_id = b.id)
           AND m.result != 'cat'
           AND (m.winner_id IS NULL OR m.winner_id != b.id)) AS losses
    FROM bots b
    WHERE b.base_name = :base_name
    ORDER BY b.version DESC
    """
)


async def get_bot_family(
    session: AsyncSession, base_name: str
) -> list[Row[Any]]:
    result = await session.execute(_BOT_FAMILY_SQL, {"base_name": base_name})
    return list(result.all())


async def list_bot_names(session: AsyncSession) -> list[str]:
    """Distinct base_names — used for the matches filter dropdown so picking
    a name selects the whole bot family, not a single version."""
    result = await session.execute(
        select(Bot.base_name).distinct().order_by(Bot.base_name)
    )
    return list(result.scalars().all())


def _match_select() -> Any:
    bx = Bot.__table__.alias("bx")
    bo = Bot.__table__.alias("bo")
    bw = Bot.__table__.alias("bw")
    return (
        select(
            Match.id,
            bx.c.versioned_name.label("bot_x"),
            bx.c.python_version.label("bot_x_python"),
            bo.c.versioned_name.label("bot_o"),
            bo.c.python_version.label("bot_o_python"),
            bw.c.versioned_name.label("winner"),
            Match.result,
            Match.played_at,
        )
        .select_from(Match)
        .join(bx, Match.bot_x_id == bx.c.id)
        .join(bo, Match.bot_o_id == bo.c.id)
        .outerjoin(bw, Match.winner_id == bw.c.id)
    ), bx, bo


async def list_matches(
    session: AsyncSession, bot_name: str | None = None
) -> list[Row[Any]]:
    """If `bot_name` is given, it's treated as a `base_name` and matches any
    version of that bot family."""
    stmt, bx, bo = _match_select()
    if bot_name:
        stmt = stmt.where(
            or_(bx.c.base_name == bot_name, bo.c.base_name == bot_name)
        )
    stmt = stmt.order_by(Match.played_at.desc())
    result = await session.execute(stmt)
    return list(result.all())


async def get_match(session: AsyncSession, match_id: int) -> Row[Any] | None:
    stmt, _, _ = _match_select()
    stmt = stmt.where(Match.id == match_id)
    result = await session.execute(stmt)
    return result.first()


async def get_moves(session: AsyncSession, match_id: int) -> list[Row[Any]]:
    result = await session.execute(
        select(
            Move.move_number,
            Move.board_state,
            Move.error,
            Bot.versioned_name.label("bot_name"),
        )
        .join(Bot, Move.bot_id == Bot.id)
        .where(Move.match_id == match_id)
        .order_by(Move.move_number)
    )
    return list(result.all())
