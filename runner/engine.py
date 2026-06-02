import subprocess
import uuid
from dataclasses import dataclass, field

Board = list[list[str]]

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


def check_winner(board: Board) -> str | None:
    for line in WINNING_LINES:
        values = [board[r][c] for r, c in line]
        if values == ["X", "X", "X"]:
            return "x_wins"
        if values == ["O", "O", "O"]:
            return "o_wins"
    if all(board[r][c] != "." for r in range(3) for c in range(3)):
        return "cat"
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


def run_bot(
    file_path: str,
    symbol: str,
    board: Board,
    python_version: str = "3",
    timeout: int = 5,
) -> tuple[Board | None, str | None]:
    stdin = f"{symbol}\n{board_to_str(board)}"
    container = f"ttt-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm",
        "--name", container,
        "--interactive",
        "--network", "none",
        "--memory", "128m",
        "--cpus", "0.5",
        "--volume", f"{file_path}:/bot.py:ro",
        f"python:{python_version}",
        "python", "/bot.py",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = proc.stdout.strip()
        if not output:
            stderr = proc.stderr.strip()
            detail = f": {stderr}" if stderr else ""
            return None, f"invalid output: empty response{detail}"
        new_board = parse_board(output)
        if new_board is None:
            return None, f"invalid output: unparseable board: {output!r}"
        return new_board, None
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", container], capture_output=True)
        return None, f"timeout after {timeout}s"
    except Exception as exc:
        return None, f"error: {exc}"


@dataclass
class Move:
    move_number: int
    player: str
    board: str
    error: str | None = None


@dataclass
class MatchResult:
    result: str
    moves: list[Move] = field(default_factory=list)


def play_match(
    bot_x_path: str,
    bot_o_path: str,
    bot_x_python: str = "3",
    bot_o_python: str = "3",
    timeout: int = 5,
) -> MatchResult:
    board: Board = [row[:] for row in EMPTY_BOARD]
    moves: list[Move] = []
    move_number = 0

    while True:
        for player, symbol, path, python_version in [
            ("x", "X", bot_x_path, bot_x_python),
            ("o", "O", bot_o_path, bot_o_python),
        ]:
            move_number += 1
            new_board, error = run_bot(path, symbol, board, python_version, timeout)

            if error or new_board is None:
                moves.append(Move(move_number, player, board_to_str(board), error))
                return MatchResult(
                    result="x_forfeit" if player == "x" else "o_forfeit",
                    moves=moves,
                )

            move_error = validate_move(board, new_board, symbol)
            if move_error:
                moves.append(
                    Move(move_number, player, board_to_str(board), move_error)
                )
                return MatchResult(
                    result="x_forfeit" if player == "x" else "o_forfeit",
                    moves=moves,
                )

            board = new_board
            moves.append(Move(move_number, player, board_to_str(board)))

            outcome = check_winner(board)
            if outcome:
                return MatchResult(result=outcome, moves=moves)
