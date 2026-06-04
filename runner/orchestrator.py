"""
Orchestrator: consumes `matches.todo`, drives the per-turn RPC game loop,
records match outcomes in Postgres.

The game-loop part (`play_match_rpc`) takes any object that exposes an async
`call(queue, payload, timeout) -> bytes`, so it's testable without a broker.
"""

import asyncio
import base64
import json
import os
import signal
from typing import Any, Protocol

import aio_pika
from sqlalchemy import select

from db.database import get_session, record_match
from db.models.bot import Bot
from messaging.client import BROKER_URL
from messaging.queue import MATCHES_QUEUE
from messaging.routing import turn_queue_for
from messaging.rpc import RpcClient
from runner.engine import (
    EMPTY_BOARD,
    Board,
    MatchResult,
    Move,
    board_to_str,
    check_winner,
    parse_board,
    validate_move,
)

TURN_TIMEOUT = float(os.environ.get("TURN_TIMEOUT", "10"))


class RpcCaller(Protocol):
    async def call(
        self, target_queue: str, payload: bytes, timeout: float = ...
    ) -> bytes: ...


def _forfeit_label(player: str) -> str:
    return "x_forfeit" if player == "x" else "o_forfeit"


async def play_match_rpc(
    rpc: RpcCaller,
    bot_x_source: bytes,
    bot_o_source: bytes,
    python_version: str,
    timeout: float = TURN_TIMEOUT,
) -> MatchResult:
    """Play one match by RPC-ing each turn to the right per-version queue."""
    board: Board = [row[:] for row in EMPTY_BOARD]
    moves: list[Move] = []
    queue_name = turn_queue_for(python_version)
    sources = (bot_x_source, bot_o_source)
    move_number = 0

    while True:
        for idx, (player, symbol) in enumerate((("x", "X"), ("o", "O"))):
            move_number += 1
            payload = json.dumps(
                {
                    "symbol": symbol,
                    "board": board_to_str(board),
                    "source_b64": base64.b64encode(sources[idx]).decode("ascii"),
                }
            ).encode()

            try:
                response_bytes = await rpc.call(queue_name, payload, timeout=timeout)
            except TimeoutError:
                moves.append(
                    Move(move_number, player, board_to_str(board),
                         f"timeout after {timeout}s")
                )
                return MatchResult(_forfeit_label(player), moves)

            response: dict[str, Any] = json.loads(response_bytes)
            error = response.get("error")
            new_board_text = response.get("board")

            if error or not new_board_text:
                moves.append(
                    Move(move_number, player, board_to_str(board), error or "no output")
                )
                return MatchResult(_forfeit_label(player), moves)

            new_board = parse_board(new_board_text)
            if new_board is None:
                moves.append(
                    Move(move_number, player, board_to_str(board),
                         f"invalid output: unparseable board: {new_board_text!r}")
                )
                return MatchResult(_forfeit_label(player), moves)

            move_error = validate_move(board, new_board, symbol)
            if move_error:
                moves.append(
                    Move(move_number, player, board_to_str(board), move_error)
                )
                return MatchResult(_forfeit_label(player), moves)

            board = new_board
            moves.append(Move(move_number, player, board_to_str(board)))

            outcome = check_winner(board)
            if outcome:
                return MatchResult(outcome, moves)


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


async def run() -> None:  # pragma: no cover
    """Connect to the broker and serve forever. Exercised by the smoke test;
    excluded from coverage because it's all wiring."""
    connection = await aio_pika.connect_robust(BROKER_URL)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)

    rpc = await RpcClient.create(channel)
    queue = await channel.declare_queue(MATCHES_QUEUE, durable=True)

    async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process():
            print(f"[orchestrator] received {message.body!r}")
            try:
                result = await handle_match_message(rpc, message.body)
                print(f"[orchestrator]   result: {result.result}")
            except Exception as exc:
                print(f"[orchestrator]   error: {exc}")

    await queue.consume(on_message)
    print("Orchestrator running. Ctrl+C to stop.")

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: stop.set_result(None) if not stop.done() else None,
        )
    try:
        await stop
    finally:
        await connection.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
