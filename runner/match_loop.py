"""
Pure game-driving logic — drives a match's per-turn RPC loop.

Takes any `RpcCaller`, so it's broker-agnostic and fully unit-testable.
"""

import base64
import json
import os

from messaging.routing import turn_queue_for
from messaging.rpc_client import RpcCaller
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


class _BotForfeit(Exception):
    """Raised by `_request_turn` to short-circuit a turn the bot blew.
    Never escapes this module — `play_match_rpc` catches it and records the
    `error` message on the resulting Move row."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


def _forfeit_label(player: str) -> str:
    return "x_forfeit" if player == "x" else "o_forfeit"


async def _request_turn(
    rpc: RpcCaller,
    queue_name: str,
    timeout: float,
    symbol: str,
    board: Board,
    source: bytes,
) -> Board:
    """One turn round-trip: send the request, validate the reply, return
    the new board. Raises `_BotForfeit` on timeout, worker-reported error,
    missing board, unparseable board, or rule-violating move."""
    payload = json.dumps(
        {
            "symbol": symbol,
            "board": board_to_str(board),
            "source_b64": base64.b64encode(source).decode("ascii"),
        }
    ).encode()

    try:
        response_bytes = await rpc.call(queue_name, payload, timeout=timeout)
    except TimeoutError:
        raise _BotForfeit(f"timeout after {timeout}s") from None

    response = json.loads(response_bytes)
    error = response.get("error")
    new_board_text = response.get("board")
    if error or not new_board_text:
        raise _BotForfeit(error or "no output")

    new_board = parse_board(new_board_text)
    if new_board is None:
        raise _BotForfeit(
            f"invalid output: unparseable board: {new_board_text!r}"
        )

    move_error = validate_move(board, new_board, symbol)
    if move_error:
        raise _BotForfeit(move_error)

    return new_board


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
    turns = (
        ("x", "X", bot_x_source),
        ("o", "O", bot_o_source),
    )
    move_number = 0

    while True:
        for player, symbol, source in turns:
            move_number += 1
            try:
                board = await _request_turn(
                    rpc, queue_name, timeout, symbol, board, source
                )
            except _BotForfeit as forfeit:
                moves.append(
                    Move(move_number, player, board_to_str(board), forfeit.error)
                )
                return MatchResult(_forfeit_label(player), moves)

            moves.append(Move(move_number, player, board_to_str(board)))
            outcome = check_winner(board)
            if outcome:
                return MatchResult(outcome, moves)
