"""
name: Vertical Blind Bot
"""
import sys

LINES = [
    [(0, 0), (0, 1), (0, 2)],
    [(1, 0), (1, 1), (1, 2)],
    [(2, 0), (2, 1), (2, 2)],
    [(0, 0), (1, 1), (2, 2)],
    [(0, 2), (1, 1), (2, 0)],
]

PREFERRED = [(1, 1), (0, 0), (0, 2), (2, 0), (2, 2), (0, 1), (1, 0), (1, 2), (2, 1)]


def find_completion(board, symbol):
    for line in LINES:
        cells = [(r, c, board[r][c]) for r, c in line]
        marks = [v for _, _, v in cells]
        if marks.count(symbol) == 2 and marks.count(".") == 1:
            for r, c, v in cells:
                if v == ".":
                    return (r, c)
    return None


data = sys.stdin.read().strip().splitlines()
symbol = data[0]
opponent = "O" if symbol == "X" else "X"
board = [row.split("|") for row in data[1:]]

move = find_completion(board, symbol)
if move is None:
    move = find_completion(board, opponent)
if move is None:
    for r, c in PREFERRED:
        if board[r][c] == ".":
            move = (r, c)
            break

r, c = move
board[r][c] = symbol
print("\n".join("|".join(row) for row in board))
