"""
Pure tic-tac-toe game logic. No I/O, no broker, no subprocesses — just board
parsing, move validation, and winner detection. Used by the orchestrator to
drive matches, and could be reused by anything else that needs to reason
about board state.
"""

import enum
from dataclasses import dataclass, field

Board = list[list[str]]


class MatchOutcome(str, enum.Enum):
    X_WINS = "x_wins"
    O_WINS = "o_wins"
    X_FORFEIT = "x_forfeit"
    O_FORFEIT = "o_forfeit"
    CAT = "cat"


EMPTY_BOARD: Board = [[".", ".", "."], [".", ".", "."], [".", ".", "."]]

WINNING_LINES: list[list[tuple[int, int]]] = [
    [(0, 0), (0, 1), (0, 2)],
    [(1, 0), (1, 1), (1, 2)],
    [(2, 0), (2, 1), (2, 2)],
    [(0, 0), (1, 0), (2, 0)],
    [(0, 1), (1, 1), (2, 1)],
    [(0, 2), (1, 2), (2, 2)],
    [(0, 0), (1, 1), (2, 2)],
    [(0, 2), (1, 1), (2, 0)],
]


def parse_board(text: str) -> Board | None:
    rows = text.strip().splitlines()
    if len(rows) != 3:
        return None
    board: Board = []
    for row in rows:
        cells = row.split("|")
        if len(cells) != 3 or not all(c in ("X", "O", ".") for c in cells):
            return None
        board.append(cells)
    return board


def board_to_str(board: Board) -> str:
    return "\n".join("|".join(row) for row in board)


def check_winner(board: Board) -> MatchOutcome | None:
    for line in WINNING_LINES:
        values = [board[r][c] for r, c in line]
        if values == ["X", "X", "X"]:
            return MatchOutcome.X_WINS
        if values == ["O", "O", "O"]:
            return MatchOutcome.O_WINS
    if all(board[r][c] != "." for r in range(3) for c in range(3)):
        return MatchOutcome.CAT
    return None


def validate_move(old_board: Board, new_board: Board, symbol: str) -> str | None:
    changes = [
        (r, c, old_board[r][c], new_board[r][c])
        for r in range(3)
        for c in range(3)
        if old_board[r][c] != new_board[r][c]
    ]
    if not changes:
        return "no move made"
    if len(changes) > 1:
        return f"{len(changes)} cells changed, expected 1"
    r, c, old, new = changes[0]
    if old != ".":
        return f"cell ({r},{c}) was already occupied"
    if new != symbol:
        return f"placed wrong symbol '{new}', expected '{symbol}'"
    return None


@dataclass
class Move:
    move_number: int
    player: str
    board: str
    error: str | None = None


@dataclass
class MatchResult:
    result: MatchOutcome
    moves: list[Move] = field(default_factory=list)
