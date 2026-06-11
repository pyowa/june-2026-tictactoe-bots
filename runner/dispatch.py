"""
End-to-end handling for one match message: fetch bot sources, drive the
game loop, persist the result.
"""

import structlog

from db.session import get_session
from entities.bot.repository import BotRepository
from entities.match.repository import MatchRepository
from messaging.queue import MatchJob
from messaging.rpc_client import RpcCaller
from runner.engine import MatchResult
from runner.match_loop import play_match_rpc

_log = structlog.get_logger()


async def fetch_bot_sources(bot_x_id: int, bot_o_id: int) -> tuple[bytes, bytes]:
    async with get_session() as session:
        bots = await BotRepository(session).by_ids([bot_x_id, bot_o_id])
    return bots[bot_x_id].source or b"", bots[bot_o_id].source or b""


async def handle_match_message(rpc: RpcCaller, body: bytes) -> MatchResult:
    """End-to-end handling of one match: fetch sources, play, persist."""
    job = MatchJob.model_validate_json(body)

    bot_x_source, bot_o_source = await fetch_bot_sources(job.bot_x_id, job.bot_o_id)
    _log.info(
        "match_started",
        correlation_id=job.correlation_id,
        bot_x_id=job.bot_x_id,
        bot_o_id=job.bot_o_id,
    )
    result = await play_match_rpc(
        rpc, bot_x_source, bot_o_source, job.python_version, job.correlation_id
    )
    _log.info(
        "match_complete",
        correlation_id=job.correlation_id,
        result=result.result,
        moves=len(result.moves),
    )
    async with get_session() as session:
        await MatchRepository(session).record(
            job.bot_x_id, job.bot_o_id, result, job.correlation_id
        )
    return result
