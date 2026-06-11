"""Move-shaped queries."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from entities.bot.model import Bot
from entities.move.model import Move


class MoveRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_match(self, match_id: int) -> list[Row[Any]]:
        """Per-move detail used by the match-detail page. Joins each move's
        `bot_id` against the bots table to project the bot's `versioned_name`
        so the template doesn't need a second lookup per row."""
        result = await self._session.execute(
            select(
                Move.move_number,
                Move.board_state,
                Move.error,
                Bot.versioned_name.label("bot_name"),
            )
            .join(Bot, Move.bot_id == Bot.id)  # pragma: no mutate
            .where(Move.match_id == match_id)
            .order_by(Move.move_number)
        )
        return list(result.all())
