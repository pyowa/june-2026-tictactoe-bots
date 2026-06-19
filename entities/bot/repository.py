"""Every query that returns Bot-shaped rows. Cross-entity queries that
return bot summaries (leaderboard, bot-family stats) live here because the
caller asks "give me bots"; the join shape is an implementation detail."""

from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, undefer

from entities.bot.model import Bot
from entities.match.model import Match
from runner.engine import MatchOutcome


def _latest_bot_cte() -> Any:
    """Build the two CTEs that find the latest version per family.

    Returns the `latest_bot` CTE — the caller only needs that one;
    `latest_per_family` is internal to this helper."""
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

    return latest_bot


def _per_version_stats(
    bot_id_expr: Any,
) -> tuple[Any, Any, Any, Any]:
    """Build the four correlated scalar subqueries for per-version stats.

    Takes the id expression to correlate against (either `latest_bot.c.id`
    from the leaderboard CTE or `Bot.id` for family).
    Returns a 4-tuple: (clean_wins, forfeit_wins, draws, losses)."""
    clean_wins = (
        select(func.count())
        .select_from(Match)
        .where(
            Match.winner_id == bot_id_expr,
            Match.result.in_((MatchOutcome.X_WINS, MatchOutcome.O_WINS)),
        )
        .scalar_subquery()
    )
    forfeit_wins = (
        select(func.count())
        .select_from(Match)
        .where(
            Match.winner_id == bot_id_expr,
            Match.result.in_((MatchOutcome.X_FORFEIT, MatchOutcome.O_FORFEIT)),
        )
        .scalar_subquery()
    )
    draws = (
        select(func.count())
        .select_from(Match)
        .where(
            or_(Match.bot_x_id == bot_id_expr, Match.bot_o_id == bot_id_expr),
            Match.result == MatchOutcome.CAT,
        )
        .scalar_subquery()
    )
    losses = (
        select(func.count())
        .select_from(Match)
        .where(
            or_(Match.bot_x_id == bot_id_expr, Match.bot_o_id == bot_id_expr),
            Match.result != MatchOutcome.CAT,
            or_(Match.winner_id.is_(None), Match.winner_id != bot_id_expr),
        )
        .scalar_subquery()
    )
    return clean_wins, forfeit_wins, draws, losses


def _lifetime_stats(
    base_name_expr: Any,
    *extra_correlate: Any,
) -> tuple[Any, Any]:
    """Build `lifetime_wins` and `lifetime_losses` subqueries.

    Lifetime W/L spans every version of the family, excluding pure
    intra-family matches so a family playing itself doesn't inflate both
    sides. `Bot` is aliased three ways (bx / bo / bw) so the joins are
    unambiguous in the generated SQL.

    Takes the base_name expression (e.g. `latest_bot.c.base_name`).

    `extra_correlate` should include any outer CTE/subquery that `base_name_expr`
    is drawn from. The `winner_not_in_family` NOT EXISTS is nested two levels
    deep; SQLAlchemy only auto-correlates one level up, so the outer CTE must
    be listed explicitly to prevent it from leaking into the inner FROM clause.

    Returns a 2-tuple: (lifetime_wins, lifetime_losses)."""
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
            bw.base_name == base_name_expr,
            or_(
                Match.bot_x_id == Match.bot_o_id,
                bx.base_name != base_name_expr,
                bo.base_name != base_name_expr,
            ),
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
            bw_inner.base_name == base_name_expr,
        )
        .correlate(Match, *extra_correlate)
        .exists()
    )

    lifetime_losses = (
        select(func.count())
        .select_from(Match)
        .join(bx, bx.id == Match.bot_x_id)
        .join(bo, bo.id == Match.bot_o_id)
        .where(
            Match.result != MatchOutcome.CAT,
            or_(bx.base_name == base_name_expr, bo.base_name == base_name_expr),
            or_(bx.base_name != base_name_expr, bo.base_name != base_name_expr),
            or_(Match.winner_id.is_(None), winner_not_in_family),
        )
        .scalar_subquery()
    )

    return lifetime_wins, lifetime_losses


def _leaderboard_query() -> Any:
    """Build the full leaderboard SELECT statement."""
    latest_bot = _latest_bot_cte()

    latest_id = latest_bot.c.id
    latest_base = latest_bot.c.base_name

    clean_wins, forfeit_wins, draws, losses = _per_version_stats(latest_id)
    lifetime_wins, lifetime_losses = _lifetime_stats(latest_base, latest_bot)

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

    return select(stats).order_by(
        (stats.c.clean_wins + stats.c.forfeit_wins).desc(),
        stats.c.submitted_at.asc(),
    )


class BotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_id(self, bot_id: int) -> Bot | None:
        result = await self._session.execute(select(Bot).where(Bot.id == bot_id))
        return result.scalar_one_or_none()

    async def by_ids(self, bot_ids: list[int]) -> dict[int, Bot]:
        """Fetch multiple bots in one query, keyed by id. Eager-loads the
        deferred `Bot.source` column via `undefer` so callers (notably the
        orchestrator's `fetch_bot_sources`) don't trip a lazy-load round-trip
        per bot when they read `.source`."""
        result = await self._session.execute(
            select(Bot).options(undefer(Bot.source)).where(Bot.id.in_(bot_ids))
        )
        return {bot.id: bot for bot in result.scalars()}

    async def by_versioned_name(self, name: str) -> Bot | None:
        result = await self._session.execute(
            select(Bot).where(Bot.versioned_name == name)
        )
        return result.scalar_one_or_none()

    async def all(self) -> list[Bot]:
        result = await self._session.scalars(select(Bot))
        return list(result.all())

    async def list_for_homepage(self) -> list[Row[Any]]:
        result = await self._session.execute(
            select(Bot.versioned_name, Bot.submitted_at).order_by(
                Bot.submitted_at.desc()
            )
        )
        return list(result.all())

    async def owner_token(self, base_name: str) -> str | None:
        result = await self._session.execute(
            select(Bot.owner_token).where(Bot.base_name == base_name).limit(1)
        )
        return result.scalar_one_or_none()

    async def next_version(self, base_name: str) -> int:
        result = await self._session.execute(
            select(func.max(Bot.version)).where(Bot.base_name == base_name)
        )
        current = result.scalar()
        return (current or 0) + 1

    async def create(
        self,
        *,
        base_name: str,
        versioned_name: str,
        version: int,
        owner_token: str,
        python_version: str = "3",  # pragma: no mutate -- trampoline
        runtime_key: str = "python-3.14",  # pragma: no mutate -- trampoline
        source: bytes | None = None,
    ) -> Bot:
        """Insert a new bot row. Flushes so `bot.id` is populated; the caller
        is responsible for committing the surrounding transaction."""
        bot = Bot(
            base_name=base_name,
            versioned_name=versioned_name,
            version=version,
            owner_token=owner_token,
            python_version=python_version,
            runtime_key=runtime_key,
            source=source,
        )
        self._session.add(bot)
        await self._session.commit()
        return bot

    async def ready_bots(self) -> list[Bot]:
        result = await self._session.scalars(
            select(Bot).where(Bot.pod_ready.is_(True))
        )
        return list(result.all())

    async def set_pod_ready(self, bot_id: int, pod_name: str) -> None:
        bot = await self._session.get(Bot, bot_id)
        if bot is not None:
            bot.pod_ready = True
            bot.pod_name = pod_name
            await self._session.commit()

    async def leaderboard(self) -> list[Row[Any]]:
        result = await self._session.execute(_leaderboard_query())
        return list(result.all())

    async def family(self, base_name: str) -> list[Row[Any]]:
        # Four correlated COUNT(*) subqueries — one per per-version stat we
        # need for the bot-family detail page. Each one filters `matches`
        # against the outer `Bot.id`, which SQLAlchemy correlates
        # automatically because we reference `Bot` columns inside the inner
        # select.
        clean_wins, forfeit_wins, draws, losses = _per_version_stats(Bot.id)

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

        result = await self._session.execute(stmt)
        return list(result.all())
