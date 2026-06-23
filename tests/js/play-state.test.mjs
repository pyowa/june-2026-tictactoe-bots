// Pure-logic tests for the human-vs-bot play reducer.
//
// Covers:
//  - initialState chooses the first turn from humanSymbol
//  - applyHumanMove ignores clicks when it isn't the human's turn
//  - applyHumanMove ignores clicks on occupied cells
//  - applyBotMove advances turn, detects winners
//  - applyForfeit reports the reason

import { test } from "node:test";
import assert from "node:assert/strict";

import {
    applyBotMove,
    applyForfeit,
    applyHumanMove,
    boardToStr,
    checkWinner,
    emptyBoard,
    initialState,
    parseBoard,
} from "../../web/static/play-state.mjs";

const HUMAN_X_CTX = {
    humanSymbol: "X",
    botSymbol: "O",
    playerName: "Matt",
    botName: "AlphaBot",
};

const HUMAN_O_CTX = {
    humanSymbol: "O",
    botSymbol: "X",
    playerName: "Matt",
    botName: "AlphaBot",
};

test("initialState with humanSymbol=X starts on human's turn", () => {
    const s = initialState(HUMAN_X_CTX);
    assert.equal(s.whose, "human");
    assert.equal(s.status, "Matt's Turn");
    assert.equal(s.ended, false);
});

test("initialState with humanSymbol=O starts on bot's turn", () => {
    const s = initialState(HUMAN_O_CTX);
    assert.equal(s.whose, "bot");
    assert.equal(s.status, "AlphaBot's Turn");
});

test("initialState board is empty 3x3", () => {
    const s = initialState(HUMAN_X_CTX);
    assert.deepEqual(s.board, emptyBoard());
});

test("applyHumanMove ignored when whose !== human", () => {
    const s = { ...initialState(HUMAN_X_CTX), whose: "bot" };
    const after = applyHumanMove(s, HUMAN_X_CTX, 0);
    assert.strictEqual(after, s);
});

test("applyHumanMove ignored when cell is occupied", () => {
    const s = initialState(HUMAN_X_CTX);
    s.board[0][0] = "O";
    const after = applyHumanMove(s, HUMAN_X_CTX, 0);
    assert.strictEqual(after, s);
});

test("applyHumanMove ignored when game has ended", () => {
    const s = { ...initialState(HUMAN_X_CTX), ended: true };
    const after = applyHumanMove(s, HUMAN_X_CTX, 0);
    assert.strictEqual(after, s);
});

test("applyHumanMove places symbol and hands turn to bot", () => {
    const s = initialState(HUMAN_X_CTX);
    const after = applyHumanMove(s, HUMAN_X_CTX, 4);
    assert.equal(after.board[1][1], "X");
    assert.equal(after.whose, "bot");
    assert.equal(after.status, "AlphaBot's Turn");
    assert.equal(after.ended, false);
});

test("applyHumanMove does not mutate previous state's board", () => {
    const s = initialState(HUMAN_X_CTX);
    applyHumanMove(s, HUMAN_X_CTX, 0);
    assert.equal(s.board[0][0], ".");
});

test("applyHumanMove that wins shows '<player> wins'", () => {
    const s = initialState(HUMAN_X_CTX);
    s.board = [
        ["X", "X", "."],
        ["O", "O", "."],
        [".", ".", "."],
    ];
    const after = applyHumanMove(s, HUMAN_X_CTX, 2);
    assert.equal(after.ended, true);
    assert.equal(after.whose, "over");
    assert.equal(after.status, "Matt wins");
});

test("applyHumanMove that completes a tie shows 'Cat game'", () => {
    const s = initialState(HUMAN_X_CTX);
    s.board = [
        ["X", "O", "X"],
        ["X", "O", "O"],
        ["O", "X", "."],
    ];
    const after = applyHumanMove(s, HUMAN_X_CTX, 8);
    assert.equal(after.ended, true);
    assert.equal(after.status, "Cat game");
});

test("applyBotMove updates board and gives turn back to human", () => {
    const s = initialState(HUMAN_X_CTX);
    s.board = [
        ["X", ".", "."],
        [".", ".", "."],
        [".", ".", "."],
    ];
    s.whose = "bot";
    const after = applyBotMove(s, HUMAN_X_CTX, "X|O|.\n.|.|.\n.|.|.");
    assert.equal(after.board[0][1], "O");
    assert.equal(after.whose, "human");
    assert.equal(after.status, "Matt's Turn");
});

test("applyBotMove that wins shows '<bot> wins'", () => {
    const s = { ...initialState(HUMAN_X_CTX), whose: "bot" };
    const after = applyBotMove(s, HUMAN_X_CTX, "O|O|O\nX|X|.\n.|.|.");
    assert.equal(after.ended, true);
    assert.equal(after.status, "AlphaBot wins");
});

test("applyBotMove that ties shows 'Cat game'", () => {
    const s = { ...initialState(HUMAN_X_CTX), whose: "bot" };
    const after = applyBotMove(s, HUMAN_X_CTX, "X|O|X\nX|O|O\nO|X|X");
    assert.equal(after.status, "Cat game");
});

test("applyForfeit produces 'Game over: <reason>'", () => {
    const s = initialState(HUMAN_X_CTX);
    const after = applyForfeit(s, "Bot took too long");
    assert.equal(after.ended, true);
    assert.equal(after.status, "Game over: Bot took too long");
});

test("checkWinner detects X win on diagonal", () => {
    assert.equal(checkWinner([
        ["X", ".", "."],
        [".", "X", "."],
        [".", ".", "X"],
    ]), "X");
});

test("checkWinner detects O win on column", () => {
    assert.equal(checkWinner([
        ["O", "X", "."],
        ["O", ".", "."],
        ["O", "X", "."],
    ]), "O");
});

test("checkWinner returns 'cat' on a full unwinnable board", () => {
    assert.equal(checkWinner([
        ["X", "O", "X"],
        ["X", "O", "O"],
        ["O", "X", "X"],
    ]), "cat");
});

test("checkWinner returns null while game is in progress", () => {
    assert.equal(checkWinner(emptyBoard()), null);
});

test("boardToStr / parseBoard round-trip", () => {
    const str = "X|O|.\n.|X|.\nO|.|.";
    assert.equal(boardToStr(parseBoard(str)), str);
});

test("emptyBoard is a fresh array each call", () => {
    const a = emptyBoard();
    const b = emptyBoard();
    a[0][0] = "X";
    assert.equal(b[0][0], ".");
});
