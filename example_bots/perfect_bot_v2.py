"""
name: Perfect Bot
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


def winner(board):
    for line in LINES:
        a, b, c = (board[r][col] for r, col in line)
        if a != "." and a == b == c:
            return a
    return None


def empties(board):
    return [(r, c) for r in range(3) for c in range(3) if board[r][c] == "."]


def minimax(board, to_move, me):
    them = "O" if me == "X" else "X"
    w = winner(board)
    if w == me:
        return 10 - sum(1 for r in range(3) for c in range(3) if board[r][c] != "."), None
    if w == them:
        return -10 + sum(1 for r in range(3) for c in range(3) if board[r][c] != "."), None
    moves = empties(board)
    if not moves:
        return 0, None

    best_score = -999 if to_move == me else 999
    best_move = moves[0]
    for r, c in moves:
        board[r][c] = to_move
        score, _ = minimax(board, them if to_move == me else me, me)
        board[r][c] = "."
        if to_move == me:
            if score > best_score:
                best_score, best_move = score, (r, c)
        else:
            if score < best_score:
                best_score, best_move = score, (r, c)
    return best_score, best_move


data = sys.stdin.read().strip().splitlines()
symbol = data[0]
board = [row.split("|") for row in data[1:]]

_, move = minimax(board, symbol, symbol)
r, c = move
board[r][c] = symbol
print("\n".join("|".join(row) for row in board))
