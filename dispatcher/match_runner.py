"""
Game loop using warm pods — one pod per bot, all turns via HTTP.

All functions are synchronous (called from a thread-pool executor).
"""

from dataclasses import dataclass
from typing import Any
from urllib.error import URLError

import structlog

from dispatcher.pods import (
    get_pod_ip,
    request_turn,
)
from runner.engine import (
    EMPTY_BOARD,
    Board,
    MatchOutcome,
    MatchResult,
    Move,
    board_to_str,
    check_winner,
    parse_board,
    validate_move,
)

_log = structlog.get_logger()


@dataclass
class _Turn:
    player: str
    symbol: str
    pod_ip: str


def _forfeit_label(player: str) -> MatchOutcome:
    return MatchOutcome.X_FORFEIT if player == "x" else MatchOutcome.O_FORFEIT


def run_match_from_pods(
    core_v1: Any,
    pod_name_x: str,
    pod_name_o: str,
    correlation_id: str = "",
    *,
    turn_timeout: float = 10.0,
) -> MatchResult:
    """Drive a game loop via HTTP using existing (permanent) bot pods.
    Looks up pod IPs from Kubernetes, runs the full game loop, and returns
    the MatchResult. Does NOT create or delete pods."""
    ip_x = get_pod_ip(core_v1, pod_name_x)
    ip_o = get_pod_ip(core_v1, pod_name_o)

    board: Board = [row[:] for row in EMPTY_BOARD]
    moves: list[Move] = []
    turns = (
        _Turn("x", "X", ip_x),
        _Turn("o", "O", ip_o),
    )
    move_number = 0

    while True:
        for turn in turns:
            move_number += 1
            _log.info(
                "turn_request",
                correlation_id=correlation_id,
                move_number=move_number,
                symbol=turn.symbol,
            )

            forfeit_error: str | None = None

            try:
                response = request_turn(
                    turn.pod_ip, turn.symbol, board_to_str(board), timeout=turn_timeout
                )
                error = response.get("error")
                new_board_text = response.get("board")
                if error or not new_board_text:
                    forfeit_error = error or "no output"
                else:
                    new_board = parse_board(new_board_text)
                    if new_board is None:
                        forfeit_error = (
                            f"invalid output: unparseable board: {new_board_text!r}"
                        )
                    else:
                        move_error = validate_move(board, new_board, turn.symbol)
                        if move_error:
                            forfeit_error = move_error
            except URLError as exc:
                forfeit_error = f"HTTP error: {exc}"

            if forfeit_error is not None:
                _log.info(
                    "turn_result",
                    correlation_id=correlation_id,
                    move_number=move_number,
                    outcome="forfeit",
                    error=forfeit_error,
                )
                moves.append(
                    Move(move_number, turn.player, board_to_str(board), forfeit_error)
                )
                return MatchResult(_forfeit_label(turn.player), moves)

            board = new_board  # type: ignore[assignment]
            _log.info(
                "turn_result",
                correlation_id=correlation_id,
                move_number=move_number,
                outcome="valid",
            )
            moves.append(Move(move_number, turn.player, board_to_str(board)))
            outcome = check_winner(board)
            if outcome:
                return MatchResult(outcome, moves)
