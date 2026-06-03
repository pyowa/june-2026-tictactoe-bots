"""
name: Double Mover Bot
"""
import sys

data = sys.stdin.read().strip().splitlines()
symbol = data[0]
board = [row.split("|") for row in data[1:]]

empties = [(r, c) for r in range(3) for c in range(3) if board[r][c] == "."]
for r, c in empties[:2]:
    board[r][c] = symbol

print("\n".join("|".join(row) for row in board))
