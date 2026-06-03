"""
name: Overwriter Bot
"""
import sys

data = sys.stdin.read().strip().splitlines()
symbol = data[0]
opponent = "O" if symbol == "X" else "X"
board = [row.split("|") for row in data[1:]]

target = None
for r in range(3):
    for c in range(3):
        if board[r][c] == opponent:
            target = (r, c)
            break
    if target:
        break

if target is None:
    for r in range(3):
        for c in range(3):
            if board[r][c] == ".":
                target = (r, c)
                break
        if target:
            break

r, c = target
board[r][c] = symbol
print("\n".join("|".join(row) for row in board))
