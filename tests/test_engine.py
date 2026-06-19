import pytest

from runner.engine import (
    Board,
    MatchOutcome,
    board_to_str,
    check_winner,
    parse_board,
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
    assert parse_board("X|Y|.\n.|.|.\n.|.|.") is None


def test_parse_board_rejects_z_symbol() -> None:
    # A second rejection case to lock the `c in ("X", "O", ".")` cell whitelist
    # — flipping it to include any letter would still pass the "Y" test if Y
    # got added to the whitelist; this catches a broader regression.
    assert parse_board("Z|.|.\n.|.|.\n.|.|.") is None


def test_parse_board_strips_trailing_whitespace_and_newlines() -> None:
    """`text.strip().splitlines()` tolerates a trailing blank line (common
    when a subprocess prints with `print()` and emits an extra `\\n`).
    Without `.strip()`, the trailing empty line would make `len(rows) == 4`
    and the board would be rejected."""
    board = parse_board("X|.|.\n.|.|.\n.|.|.\n\n")
    assert board == [["X", ".", "."], [".", ".", "."], [".", ".", "."]]


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
        ("X|X|X\n.|.|.\n.|.|.", MatchOutcome.X_WINS),
        (".|.|.\nX|X|X\n.|.|.", MatchOutcome.X_WINS),
        (".|.|.\n.|.|.\nX|X|X", MatchOutcome.X_WINS),
        ("O|O|O\n.|.|.\n.|.|.", MatchOutcome.O_WINS),
        (".|.|.\nO|O|O\n.|.|.", MatchOutcome.O_WINS),
        (".|.|.\n.|.|.\nO|O|O", MatchOutcome.O_WINS),
        ("X|.|.\nX|.|.\nX|.|.", MatchOutcome.X_WINS),
        (".|X|.\n.|X|.\n.|X|.", MatchOutcome.X_WINS),
        (".|.|X\n.|.|X\n.|.|X", MatchOutcome.X_WINS),
        ("O|.|.\nO|.|.\nO|.|.", MatchOutcome.O_WINS),
        (".|O|.\n.|O|.\n.|O|.", MatchOutcome.O_WINS),
        (".|.|O\n.|.|O\n.|.|O", MatchOutcome.O_WINS),
        ("X|.|.\n.|X|.\n.|.|X", MatchOutcome.X_WINS),
        (".|.|X\n.|X|.\nX|.|.", MatchOutcome.X_WINS),
        ("O|.|.\n.|O|.\n.|.|O", MatchOutcome.O_WINS),
        (".|.|O\n.|O|.\nO|.|.", MatchOutcome.O_WINS),
        ("X|O|X\nO|O|X\nX|X|O", MatchOutcome.CAT),
        ("X|.|.\n.|.|.\n.|.|.", None),
        (EMPTY, None),
    ],
)
def test_check_winner(board_text: str, expected: MatchOutcome | None) -> None:
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
