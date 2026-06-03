"""
name: Offense Only Bot
"""
import sys

LINES = [
    [(0, 0), (0, 1), (0, 2)],
    [(1, 0), (1, 1), (1, 2)],
    [(2, 0), (2, 1), (2, 2)],
    [(0, 0), (1, 0), (2, 0)],
    [(0, 1), (1, 1), (2, 1)],
    [(0, 2), (1, 2), (2, 2)],
    [(0, 0), (1, 1), (2, 2)],
    [(0, 2), (1, 1), (2, 0)],
]

AGGRESSIVE_ORDER = [(1, 1), (0, 0), (0, 2), (2, 0), (2, 2), (0, 1), (1, 0), (1, 2), (2, 1)]


def find_win(board, symbol):
    for line in LINES:
        cells = [(r, c, board[r][c]) for r, c in line]
        marks = [v for _, _, v in cells]
        if marks.count(symbol) == 2 and marks.count(".") == 1:
            for r, c, v in cells:
                if v == ".":
                    return (r, c)
    return None


def score_move(board, symbol, r, c):
    board[r][c] = symbol
    threats = 0
    for line in LINES:
        marks = [board[lr][lc] for lr, lc in line]
        if marks.count(symbol) == 2 and marks.count(".") == 1:
            threats += 1
    board[r][c] = "."
    return threats


data = sys.stdin.read().strip().splitlines()
symbol = data[0]
board = [row.split("|") for row in data[1:]]

move = find_win(board, symbol)
if move is None:
    empties = [(r, c) for r in range(3) for c in range(3) if board[r][c] == "."]
    best_threats = -1
    for r, c in AGGRESSIVE_ORDER:
        if board[r][c] != ".":
            continue
        t = score_move(board, symbol, r, c)
        if t > best_threats:
            best_threats = t
            move = (r, c)

r, c = move
board[r][c] = symbol
print("\n".join("|".join(row) for row in board))
