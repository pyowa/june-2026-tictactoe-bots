"""
Game loop using warm pods — one pod per bot, all turns via HTTP.

All functions are synchronous (called from a thread-pool executor).
"""

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
    MatchResult,
    Move,
    board_to_str,
    check_winner,
    parse_board,
    validate_move,
)

_log = structlog.get_logger()


def _forfeit_label(player: str) -> str:
    return "x_forfeit" if player == "x" else "o_forfeit"


def _forfeit(
    correlation_id: str, move_number: int, error: str
) -> tuple[None, str]:
    _log.info(
        "turn_result",
        correlation_id=correlation_id,
        move_number=move_number,
        outcome="forfeit",
        error=error,
    )
    return None, error


def _play_turn(
    player: str,
    symbol: str,
    pod_ip: str,
    board: Board,
    move_number: int,
    correlation_id: str,
    timeout: float,
) -> tuple[Board, None] | tuple[None, str]:
    """Returns (new_board, None) on success or (None, forfeit_error) on failure."""
    _log.info(
        "turn_request",
        correlation_id=correlation_id,
        move_number=move_number,
        symbol=symbol,
    )

    try:
        response = request_turn(pod_ip, symbol, board_to_str(board), timeout=timeout)
        error = response.get("error")
        new_board_text = response.get("board")
        if error or not new_board_text:
            return _forfeit(correlation_id, move_number, error or "no output")
        new_board = parse_board(new_board_text)
        if new_board is None:
            return _forfeit(
                correlation_id, move_number,
                f"invalid output: unparseable board: {new_board_text!r}",
            )
        move_error = validate_move(board, new_board, symbol)
        if move_error:
            return _forfeit(correlation_id, move_number, move_error)
    except URLError as exc:
        return _forfeit(correlation_id, move_number, f"HTTP error: {exc}")

    _log.info(
        "turn_result",
        correlation_id=correlation_id,
        move_number=move_number,
        outcome="valid",
    )
    return new_board, None  # type: ignore[return-value]



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
        ("x", "X", ip_x),
        ("o", "O", ip_o),
    )
    move_number = 0

    while True:
        for player, symbol, pod_ip in turns:
            move_number += 1
            new_board, forfeit_error = _play_turn(
                player, symbol, pod_ip, board, move_number, correlation_id, turn_timeout
            )
            if forfeit_error is not None:
                moves.append(
                    Move(move_number, player, board_to_str(board), forfeit_error)
                )
                return MatchResult(_forfeit_label(player), moves)

            board = new_board  # type: ignore[assignment]
            moves.append(Move(move_number, player, board_to_str(board)))
            outcome = check_winner(board)
            if outcome:
                return MatchResult(outcome, moves)
