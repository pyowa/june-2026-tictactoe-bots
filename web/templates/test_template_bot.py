"""Sample tests for a tic-tac-toe bot.

Import your bot's `main()` and call it directly. Pipe input by replacing
`sys.stdin`; capture output with pytest's `capsys` fixture.
"""

import io
import sys

from your_bot import main


def feed_and_run(symbol: str, board: str, monkeypatch, capsys) -> str:
    """Pipe `<symbol>\\n<board>` into main()'s stdin, return what it prints."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{symbol}\n{board}\n"))
    main()
    return capsys.readouterr().out


def test_bot_makes_a_move_on_empty_board(monkeypatch, capsys) -> None:
    output = feed_and_run("X", ".|.|.\n.|.|.\n.|.|.", monkeypatch, capsys)
    cells = "".join(output.strip().splitlines()).replace("|", "")
    assert cells.count("X") == 1, "bot should place exactly one X"
    assert cells.count(".") == 8, "all other cells should be empty"


def test_bot_blocks_opponent_about_to_win(monkeypatch, capsys) -> None:
    # X has two-in-a-row across the top; bot O must block the third cell.
    output = feed_and_run("O", "X|X|.\n.|.|.\n.|.|.", monkeypatch, capsys)
    top_row = output.strip().splitlines()[0].split("|")
    assert top_row[2] == "O", "bot should block by playing top-right"
