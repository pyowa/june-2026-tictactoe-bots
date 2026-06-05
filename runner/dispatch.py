"""
End-to-end handling for one match message: fetch bot sources, drive the
game loop, persist the result.
"""

import json

from sqlalchemy import select

from db.database import get_session, record_match
from db.models.bot import Bot
from runner.engine import MatchResult
from runner.match_loop import RpcCaller, play_match_rpc


async def fetch_bot_sources(bot_x_id: int, bot_o_id: int) -> tuple[bytes, bytes]:
    async with get_session() as session:
        result = await session.execute(
            select(Bot.id, Bot.source).where(Bot.id.in_([bot_x_id, bot_o_id]))
        )
        rows = {row.id: row.source for row in result}
    return rows[bot_x_id], rows[bot_o_id]


async def handle_match_message(
    rpc: RpcCaller, body: bytes
) -> MatchResult:
    """End-to-end handling of one match: fetch sources, play, persist."""
    payload = json.loads(body)
    bot_x_id = int(payload["bot_x_id"])
    bot_o_id = int(payload["bot_o_id"])
    python_version = str(payload["python_version"])

    bot_x_source, bot_o_source = await fetch_bot_sources(bot_x_id, bot_o_id)
    result = await play_match_rpc(
        rpc, bot_x_source, bot_o_source, python_version
    )
    async with get_session() as session:
        await record_match(session, bot_x_id, bot_o_id, result)
    return result
