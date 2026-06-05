import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import (
    Engine,
    and_,
    create_engine,
    func,
    or_,
    select,
)
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import aliased
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


async def get_leaderboard(session: AsyncSession) -> list[Row[Any]]:
    # Mirror the original raw SQL's structure exactly: two CTEs (latest-version
    # per family, then the actual latest-bot row) feeding six correlated COUNT
    # subqueries. Aliased Bot copies stand in for the `bx`, `bo`, `bw` aliases
    # in the original (Bot used twice in one query — once as the outer "this
    # family's latest version", once as the joined opponent/winner row in the
    # lifetime W/L subqueries).
    latest_per_family = (
        select(
            Bot.base_name.label("base_name"),
            func.max(Bot.version).label("max_v"),
        )
        .group_by(Bot.base_name)
        .cte("latest_per_family")
    )

    latest_bot = (
        select(
            Bot.id.label("id"),
            Bot.base_name.label("base_name"),
            Bot.versioned_name.label("versioned_name"),
            Bot.submitted_at.label("submitted_at"),
        )
        .join(
            latest_per_family,
            and_(
                latest_per_family.c.base_name == Bot.base_name,
                latest_per_family.c.max_v == Bot.version,
            ),
        )
        .cte("latest_bot")
    )

    lb_id = latest_bot.c.id
    lb_base = latest_bot.c.base_name

    # Per-version stats (only the latest version of this family).
    clean_wins = (
        select(func.count())
        .select_from(Match)
        .where(
            Match.winner_id == lb_id,
            Match.result.in_(("x_wins", "o_wins")),
        )
        .scalar_subquery()
    )
    forfeit_wins = (
        select(func.count())
        .select_from(Match)
        .where(
            Match.winner_id == lb_id,
            Match.result.in_(("x_forfeit", "o_forfeit")),
        )
        .scalar_subquery()
    )
    draws = (
        select(func.count())
        .select_from(Match)
        .where(
            or_(Match.bot_x_id == lb_id, Match.bot_o_id == lb_id),
            Match.result == "cat",
        )
        .scalar_subquery()
    )
    losses = (
        select(func.count())
        .select_from(Match)
        .where(
            or_(Match.bot_x_id == lb_id, Match.bot_o_id == lb_id),
            Match.result != "cat",
            or_(Match.winner_id.is_(None), Match.winner_id != lb_id),
        )
        .scalar_subquery()
    )

    # Lifetime W/L across every version of the family, excluding pure
    # intra-family matches so a family playing itself doesn't inflate both
    # sides. `Bot` is aliased three ways (bx / bo / bw) so the joins are
    # unambiguous in the generated SQL.
    bx = aliased(Bot, name="bx")
    bo = aliased(Bot, name="bo")
    bw = aliased(Bot, name="bw")

    lifetime_wins = (
        select(func.count())
        .select_from(Match)
        .join(bx, bx.id == Match.bot_x_id)
        .join(bo, bo.id == Match.bot_o_id)
        .join(bw, bw.id == Match.winner_id)
        .where(
            bw.base_name == lb_base,
            or_(bx.base_name != lb_base, bo.base_name != lb_base),
        )
        .scalar_subquery()
    )

    # The NOT EXISTS subquery: "the winner of this match does NOT belong to
    # the current family." Implemented as a separate scalar subquery whose
    # bots row is yet another alias (bw_inner) so it doesn't conflict with bw.
    # `.correlate(latest_bot)` is required: this subquery is nested two levels
    # deep (inside lifetime_losses, which itself is a subquery of the outer
    # `SELECT FROM latest_bot`), and SQLAlchemy will only auto-correlate one
    # level up. Without the explicit correlate, `latest_bot` would be added
    # to the inner FROM clause and the NOT EXISTS would be uncorrelated.
    bw_inner = aliased(Bot, name="bw_inner")
    winner_not_in_family = ~(
        select(1)
        .select_from(bw_inner)
        .where(
            bw_inner.id == Match.winner_id,
            bw_inner.base_name == lb_base,
        )
        .correlate(latest_bot, Match)
        .exists()
    )

    lifetime_losses = (
        select(func.count())
        .select_from(Match)
        .join(bx, bx.id == Match.bot_x_id)
        .join(bo, bo.id == Match.bot_o_id)
        .where(
            Match.result != "cat",
            or_(bx.base_name == lb_base, bo.base_name == lb_base),
            or_(bx.base_name != lb_base, bo.base_name != lb_base),
            or_(Match.winner_id.is_(None), winner_not_in_family),
        )
        .scalar_subquery()
    )

    stats = (
        select(
            latest_bot.c.base_name.label("base_name"),
            latest_bot.c.versioned_name.label("versioned_name"),
            latest_bot.c.submitted_at.label("submitted_at"),
            clean_wins.label("clean_wins"),
            forfeit_wins.label("forfeit_wins"),
            draws.label("draws"),
            losses.label("losses"),
            lifetime_wins.label("lifetime_wins"),
            lifetime_losses.label("lifetime_losses"),
        )
        .select_from(latest_bot)
        .cte("stats")
    )

    stmt = select(stats).order_by(
        (stats.c.clean_wins + stats.c.forfeit_wins).desc(),
        stats.c.submitted_at.asc(),
    )

    result = await session.execute(stmt)
    return list(result.all())


async def get_bot_family(
    session: AsyncSession, base_name: str
) -> list[Row[Any]]:
    # Four correlated COUNT(*) subqueries — one per per-version stat we need
    # for the bot-family detail page. Each one filters `matches` against the
    # outer `Bot.id`, which SQLAlchemy correlates automatically because we
    # reference `Bot` columns inside the inner select.
    clean_wins = (
        select(func.count())
        .select_from(Match)
        .where(
            Match.winner_id == Bot.id,
            Match.result.in_(("x_wins", "o_wins")),
        )
        .scalar_subquery()
    )
    forfeit_wins = (
        select(func.count())
        .select_from(Match)
        .where(
            Match.winner_id == Bot.id,
            Match.result.in_(("x_forfeit", "o_forfeit")),
        )
        .scalar_subquery()
    )
    draws = (
        select(func.count())
        .select_from(Match)
        .where(
            or_(Match.bot_x_id == Bot.id, Match.bot_o_id == Bot.id),
            Match.result == "cat",
        )
        .scalar_subquery()
    )
    losses = (
        select(func.count())
        .select_from(Match)
        .where(
            or_(Match.bot_x_id == Bot.id, Match.bot_o_id == Bot.id),
            Match.result != "cat",
            or_(Match.winner_id.is_(None), Match.winner_id != Bot.id),
        )
        .scalar_subquery()
    )

    stmt = (
        select(
            Bot.versioned_name.label("versioned_name"),
            Bot.version.label("version"),
            Bot.submitted_at.label("submitted_at"),
            clean_wins.label("clean_wins"),
            forfeit_wins.label("forfeit_wins"),
            draws.label("draws"),
            losses.label("losses"),
        )
        .where(Bot.base_name == base_name)
        .order_by(Bot.version.desc())
    )

    result = await session.execute(stmt)
    return list(result.all())


def _match_select() -> Any:
    bx = Bot.__table__.alias("bx")
    bo = Bot.__table__.alias("bo")
    bw = Bot.__table__.alias("bw")
    return (
        select(
            Match.id,
            bx.c.versioned_name.label("bot_x"),
            bx.c.base_name.label("bot_x_base"),
            bx.c.python_version.label("bot_x_python"),
            bo.c.versioned_name.label("bot_o"),
            bo.c.base_name.label("bot_o_base"),
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


async def get_match(
    session: AsyncSession,
    match_id: int,
    bot_base_name: str | None = None,
) -> Row[Any] | None:
    """Fetch one match by id. If `bot_base_name` is provided, the match must
    involve that bot family on either side — otherwise returns None (the
    caller turns this into a 404). Used by the nested
    `/bots/<name>/matches/<id>` route so a wrong bot/match combination
    doesn't return a misleading page."""
    stmt, bx, bo = _match_select()
    stmt = stmt.where(Match.id == match_id)
    if bot_base_name is not None:
        stmt = stmt.where(
            or_(bx.c.base_name == bot_base_name, bo.c.base_name == bot_base_name)
        )
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
