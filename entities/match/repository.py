"""Match-shaped queries. The select used to render a match row is a single
join across `matches`, `bots × 3` (x / o / winner aliases); it's shared
between by_id, list_all, and list_for_bot, so the construction is captured
in the module-private `_match_select()` helper."""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from entities.bot.model import Bot
from entities.match.model import Match
from entities.move.model import Move
from runner.engine import MatchOutcome, MatchResult


def _match_select() -> Any:
    bx = Bot.__table__.alias("bx")  # pragma: no mutate -- cosmetic SQL alias
    bo = Bot.__table__.alias("bo")  # pragma: no mutate
    bw = Bot.__table__.alias("bw")  # pragma: no mutate
    return (
        (
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
        ),
        bx,
        bo,
    )


class MatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_id(
        self, match_id: int, *, bot_base_name: str | None = None
    ) -> Row[Any] | None:
        """Fetch one match by id. If `bot_base_name` is provided, the match
        must involve that bot family on either side — otherwise returns None
        (the caller turns this into a 404). Used by the nested
        `/bots/<name>/matches/<id>` route so a wrong bot/match combination
        doesn't return a misleading page."""
        stmt, bx, bo = _match_select()
        stmt = stmt.where(Match.id == match_id)
        if bot_base_name is not None:
            stmt = stmt.where(
                or_(bx.c.base_name == bot_base_name, bo.c.base_name == bot_base_name)
            )
        result = await self._session.execute(stmt)
        return result.first()

    async def list_all(self) -> list[Row[Any]]:
        stmt, _bx, _bo = _match_select()
        stmt = stmt.order_by(Match.played_at.desc())
        result = await self._session.execute(stmt)
        return list(result.all())

    async def list_for_bot(self, base_name: str) -> list[Row[Any]]:
        """List every match involving any version of the given bot family."""
        stmt, bx, bo = _match_select()
        stmt = stmt.where(
            or_(bx.c.base_name == base_name, bo.c.base_name == base_name)
        ).order_by(Match.played_at.desc())
        result = await self._session.execute(stmt)
        return list(result.all())

    async def record(
        self, bot_x_id: int, bot_o_id: int, result: MatchResult, correlation_id: str
    ) -> None:
        """Persist a completed match and its moves."""
        if result.result in (MatchOutcome.X_WINS, MatchOutcome.O_FORFEIT):
            winner_id: int | None = bot_x_id
        elif result.result in (MatchOutcome.O_WINS, MatchOutcome.X_FORFEIT):
            winner_id = bot_o_id
        else:
            winner_id = None

        match = Match(
            bot_x_id=bot_x_id,
            bot_o_id=bot_o_id,
            winner_id=winner_id,
            result=result.result,
            correlation_id=correlation_id,
        )
        self._session.add(match)
        await self._session.flush()  # populates match.id

        for move in result.moves:
            bot_id = bot_x_id if move.player == "x" else bot_o_id
            self._session.add(
                Move(
                    match_id=match.id,
                    move_number=move.move_number,
                    bot_id=bot_id,
                    board_state=move.board,
                    error=move.error,
                )
            )
        await self._session.commit()
