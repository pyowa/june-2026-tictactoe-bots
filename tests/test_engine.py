import textwrap
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest

from runner.engine import (
    Board,
    MatchResult,
    Move,
    board_to_str,
    check_winner,
    parse_board,
    play_match,
    run_bot,
    validate_move,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMPTY = ".|.|.\n.|.|.\n.|.|."


def b(text: str) -> Board:
    board = parse_board(text)
    assert board is not None
    return board


def write_bot(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source))
    return str(path)


def make_proc(stdout: str = "", returncode: int = 0) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# parse_board
# ---------------------------------------------------------------------------


def test_parse_board_valid() -> None:
    board = parse_board("X|.|O\n.|X|.\nO|.|X")
    assert board == [["X", ".", "O"], [".", "X", "."], ["O", ".", "X"]]


def test_parse_board_too_few_rows() -> None:
    assert parse_board("X|.|.\n.|.|.") is None


def test_parse_board_too_many_rows() -> None:
    assert parse_board("X|.|.\n.|.|.\n.|.|.\n.|.|.") is None


def test_parse_board_wrong_cell_count() -> None:
    assert parse_board("X|.\n.|.|.\n.|.|.") is None


def test_parse_board_invalid_symbol() -> None:
    assert parse_board("X|.|.\n.|Z|.\n.|.|.") is None


# ---------------------------------------------------------------------------
# board_to_str
# ---------------------------------------------------------------------------


def test_board_to_str_roundtrips() -> None:
    text = "X|.|O\n.|X|.\nO|.|X"
    assert board_to_str(b(text)) == text


# ---------------------------------------------------------------------------
# check_winner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "board_text,expected",
    [
        # X rows
        ("X|X|X\n.|.|.\n.|.|.", "x_wins"),
        (".|.|.\nX|X|X\n.|.|.", "x_wins"),
        (".|.|.\n.|.|.\nX|X|X", "x_wins"),
        # O rows
        ("O|O|O\n.|.|.\n.|.|.", "o_wins"),
        (".|.|.\nO|O|O\n.|.|.", "o_wins"),
        (".|.|.\n.|.|.\nO|O|O", "o_wins"),
        # X cols
        ("X|.|.\nX|.|.\nX|.|.", "x_wins"),
        (".|X|.\n.|X|.\n.|X|.", "x_wins"),
        (".|.|X\n.|.|X\n.|.|X", "x_wins"),
        # O cols
        ("O|.|.\nO|.|.\nO|.|.", "o_wins"),
        (".|O|.\n.|O|.\n.|O|.", "o_wins"),
        (".|.|O\n.|.|O\n.|.|O", "o_wins"),
        # X diagonals
        ("X|.|.\n.|X|.\n.|.|X", "x_wins"),
        (".|.|X\n.|X|.\nX|.|.", "x_wins"),
        # O diagonals
        ("O|.|.\n.|O|.\n.|.|O", "o_wins"),
        (".|.|O\n.|O|.\nO|.|.", "o_wins"),
        # Cat
        ("X|O|X\nO|O|X\nX|X|O", "cat"),
        # In progress
        ("X|.|.\n.|.|.\n.|.|.", None),
        (EMPTY, None),
    ],
)
def test_check_winner(board_text: str, expected: str | None) -> None:
    assert check_winner(b(board_text)) == expected


# ---------------------------------------------------------------------------
# validate_move
# ---------------------------------------------------------------------------


def test_validate_move_valid_x() -> None:
    old = b(EMPTY)
    new = b("X|.|.\n.|.|.\n.|.|.")
    assert validate_move(old, new, "X") is None


def test_validate_move_valid_o() -> None:
    old = b("X|.|.\n.|.|.\n.|.|.")
    new = b("X|.|.\n.|O|.\n.|.|.")
    assert validate_move(old, new, "O") is None


def test_validate_move_no_move() -> None:
    board = b(EMPTY)
    assert validate_move(board, board, "X") == "no move made"


def test_validate_move_multiple_cells_changed() -> None:
    old = b(EMPTY)
    new = b("X|.|.\n.|X|.\n.|.|.")
    error = validate_move(old, new, "X")
    assert error is not None
    assert "2 cells" in error


def test_validate_move_overwrites_occupied() -> None:
    old = b("X|.|.\n.|.|.\n.|.|.")
    new = b("O|.|.\n.|.|.\n.|.|.")
    error = validate_move(old, new, "O")
    assert error is not None
    assert "already occupied" in error


def test_validate_move_wrong_symbol() -> None:
    old = b(EMPTY)
    new = b("O|.|.\n.|.|.\n.|.|.")
    error = validate_move(old, new, "X")
    assert error is not None
    assert "wrong symbol" in error


# ---------------------------------------------------------------------------
# run_bot — unit tests via mocked subprocess
# ---------------------------------------------------------------------------


def test_run_bot_uses_docker_image(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    valid_board = "X|.|.\n.|.|.\n.|.|."
    with patch("runner.engine.subprocess.run", return_value=make_proc(valid_board)) as mock_run:
        run_bot(path, "X", b(EMPTY), python_version="3.11")
    cmd = mock_run.call_args[0][0]
    assert "python:3.11" in cmd
    assert "docker" == cmd[0]


def test_run_bot_mounts_bot_file(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    valid_board = "X|.|.\n.|.|.\n.|.|."
    with patch("runner.engine.subprocess.run", return_value=make_proc(valid_board)) as mock_run:
        run_bot(path, "X", b(EMPTY))
    cmd = mock_run.call_args[0][0]
    volume_idx = cmd.index("--volume")
    assert cmd[volume_idx + 1].startswith(path)
    assert "/bot.py:ro" in cmd[volume_idx + 1]


def test_run_bot_disables_network(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    valid_board = "X|.|.\n.|.|.\n.|.|."
    with patch("runner.engine.subprocess.run", return_value=make_proc(valid_board)) as mock_run:
        run_bot(path, "X", b(EMPTY))
    cmd = mock_run.call_args[0][0]
    assert "--network" in cmd
    assert "none" in cmd


def test_run_bot_valid_output_returns_board(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    valid_board = "X|.|.\n.|.|.\n.|.|."
    with patch("runner.engine.subprocess.run", return_value=make_proc(valid_board)):
        new_board, error = run_bot(path, "X", b(EMPTY))
    assert error is None
    assert new_board is not None
    assert new_board[0][0] == "X"


def test_run_bot_empty_output(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    with patch("runner.engine.subprocess.run", return_value=make_proc("")):
        new_board, error = run_bot(path, "X", b(EMPTY))
    assert new_board is None
    assert error is not None
    assert "empty response" in error


def test_run_bot_empty_output_includes_stderr(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    proc = CompletedProcess(args=[], returncode=1, stdout="", stderr="NameError: name 'x' is not defined")
    with patch("runner.engine.subprocess.run", return_value=proc):
        new_board, error = run_bot(path, "X", b(EMPTY))
    assert new_board is None
    assert error is not None
    assert "empty response" in error
    assert "NameError" in error


def test_run_bot_unparseable_output(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    with patch("runner.engine.subprocess.run", return_value=make_proc("not a board")):
        new_board, error = run_bot(path, "X", b(EMPTY))
    assert new_board is None
    assert error is not None
    assert "unparseable" in error


def test_run_bot_timeout_kills_container(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    with patch("runner.engine.subprocess.run") as mock_run:
        mock_run.side_effect = [TimeoutExpired(cmd=[], timeout=5), MagicMock()]
        new_board, error = run_bot(path, "X", b(EMPTY), timeout=5)
    assert new_board is None
    assert error is not None
    assert "timeout" in error
    # Second call should be docker kill
    kill_cmd = mock_run.call_args_list[1][0][0]
    assert kill_cmd[0] == "docker"
    assert kill_cmd[1] == "kill"


def test_run_bot_exception_returns_error(tmp_path: Path) -> None:
    path = write_bot(tmp_path, "bot.py", "")
    with patch("runner.engine.subprocess.run", side_effect=OSError("docker not found")):
        new_board, error = run_bot(path, "X", b(EMPTY))
    assert new_board is None
    assert error is not None
    assert "error:" in error


# ---------------------------------------------------------------------------
# play_match — unit tests with mocked run_bot
# ---------------------------------------------------------------------------

# Board sequences for mocked matches.
#
# X wins in 5 moves (X takes top row, O takes middle row first two):
#   X|.|.  X|.|.  X|X|.  X|X|.  X|X|X
#   .|.|.  O|.|.  O|.|.  O|O|.  O|O|.
#   .|.|.  .|.|.  .|.|.  .|.|.  .|.|.
X_WINS_BOARDS = [
    "X|.|.\n.|.|.\n.|.|.",
    "X|.|.\nO|.|.\n.|.|.",
    "X|X|.\nO|.|.\n.|.|.",
    "X|X|.\nO|O|.\n.|.|.",
    "X|X|X\nO|O|.\n.|.|.",
]

# O wins in 6 moves (O takes top row, X scattered):
#   .|.|.  O|.|.  O|.|.  O|O|.  O|O|.  O|O|O
#   .|.|.  .|.|.  .|.|X  .|.|X  .|.|X  .|.|X
#   X|.|.  X|.|.  X|.|.  X|.|.  X|.|X  X|.|X
O_WINS_BOARDS = [
    ".|.|.\n.|.|.\nX|.|.",
    "O|.|.\n.|.|.\nX|.|.",
    "O|.|.\n.|.|X\nX|.|.",
    "O|O|.\n.|.|X\nX|.|.",
    "O|O|.\n.|.|X\nX|.|X",
    "O|O|O\n.|.|X\nX|.|X",
]

# Cat game in 9 moves:
#   X|O|X  (verified: no winner — anti-diag X,O,X; diag X,O,X)
#   O|O|X
#   X|X|O
CAT_BOARDS = [
    "X|.|.\n.|.|.\n.|.|.",
    "X|O|.\n.|.|.\n.|.|.",
    "X|O|X\n.|.|.\n.|.|.",
    "X|O|X\nO|.|.\n.|.|.",
    "X|O|X\nO|.|.\nX|.|.",
    "X|O|X\nO|O|.\nX|.|.",
    "X|O|X\nO|O|.\nX|X|.",
    "X|O|X\nO|O|.\nX|X|O",
    "X|O|X\nO|O|X\nX|X|O",
]


def _boards_to_run_bot_returns(
    boards: list[str],
) -> list[tuple[Board | None, str | None]]:
    return [(b(board), None) for board in boards]


def test_play_match_x_wins(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    returns = _boards_to_run_bot_returns(X_WINS_BOARDS)
    with patch("runner.engine.run_bot", side_effect=returns):
        result = play_match(bot, bot)
    assert result.result == "x_wins"
    assert len(result.moves) == 5
    assert all(m.error is None for m in result.moves)


def test_play_match_o_wins(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    returns = _boards_to_run_bot_returns(O_WINS_BOARDS)
    with patch("runner.engine.run_bot", side_effect=returns):
        result = play_match(bot, bot)
    assert result.result == "o_wins"
    assert len(result.moves) == 6


def test_play_match_cat(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    returns = _boards_to_run_bot_returns(CAT_BOARDS)
    with patch("runner.engine.run_bot", side_effect=returns):
        result = play_match(bot, bot)
    assert result.result == "cat"
    assert len(result.moves) == 9


def test_play_match_x_forfeits_on_error(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    with patch("runner.engine.run_bot", return_value=(None, "I crashed")):
        result = play_match(bot, bot)
    assert result.result == "x_forfeit"
    assert len(result.moves) == 1
    assert result.moves[0].error == "I crashed"


def test_play_match_o_forfeits_on_error(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    first_move = (b(X_WINS_BOARDS[0]), None)
    forfeit = (None, "timeout after 5s")
    with patch("runner.engine.run_bot", side_effect=[first_move, forfeit]):
        result = play_match(bot, bot)
    assert result.result == "o_forfeit"
    assert len(result.moves) == 2
    assert result.moves[1].error == "timeout after 5s"


def test_play_match_x_forfeits_on_invalid_move(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    bad_board = b("O|.|.\n.|.|.\n.|.|.")  # X's turn but placed O
    with patch("runner.engine.run_bot", return_value=(bad_board, None)):
        result = play_match(bot, bot)
    assert result.result == "x_forfeit"
    assert result.moves[0].error is not None
    assert "wrong symbol" in result.moves[0].error


def test_play_match_o_forfeits_on_invalid_move(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    good_x = (b(X_WINS_BOARDS[0]), None)
    bad_o = (b("X|X|.\n.|.|.\n.|.|."), None)  # O's turn but placed X
    with patch("runner.engine.run_bot", side_effect=[good_x, bad_o]):
        result = play_match(bot, bot)
    assert result.result == "o_forfeit"
    assert result.moves[1].error is not None


def test_play_match_move_players_alternate(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    returns = _boards_to_run_bot_returns(X_WINS_BOARDS)
    with patch("runner.engine.run_bot", side_effect=returns):
        result = play_match(bot, bot)
    players = [m.player for m in result.moves]
    assert players == ["x", "o", "x", "o", "x"]


def test_play_match_passes_python_versions_to_run_bot(tmp_path: Path) -> None:
    bot = str(tmp_path / "bot.py")
    captured: list[str] = []

    def fake_run_bot(
        path: str, symbol: str, board: Board,
        python_version: str = "3", timeout: int = 5
    ) -> tuple[Board | None, str | None]:
        captured.append(python_version)
        return None, "forfeit"

    with patch("runner.engine.run_bot", side_effect=fake_run_bot):
        play_match(bot, bot, bot_x_python="3.11", bot_o_python="3.12")

    assert captured[0] == "3.11"
