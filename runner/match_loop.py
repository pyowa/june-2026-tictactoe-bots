"""
Pure game-driving logic — drives a match's per-turn RPC loop.

Takes any `RpcCaller`, so it's broker-agnostic and fully unit-testable.
"""

import base64
import json
import os
from typing import Any, Protocol

from messaging.routing import turn_queue_for
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
