"""
name: Your Bot Name
python: 3.14
"""

import sys


def read_board() -> tuple[str, list[list[str]]]:
    """
    Input:
        X
        X|.|.
        .|O|.
        .|.|.
    Output:
        (
            'X',
            [
                ['X', '.', '.'],
                ['.', 'O', '.'],
                ['.', '.', '.'],
            ]
        )
    """
    data = sys.stdin.read().strip().splitlines()
    symbol = data[0]
    return symbol, [row.split("|") for row in data[1:]]


def write_board(board: list[list[str]]) -> None:
    """Print the board to stdout, one row per line, cells separated by `|`."""
    for row in board:
        print("|".join(row))


def main() -> None:
    symbol, board = read_board()

    # TODO: pick an empty cell and replace it with `symbol`.
    # The board is a 3x3 list of cells, each "X", "O", or ".".

    write_board(board)


if __name__ == "__main__":
    main()
