import { test } from "node:test";
import assert from "node:assert/strict";

import {
    initialState,
    nextState,
} from "../../web/static/match-player-state.mjs";

const SAMPLE_MOVES = [
    { move_number: 1, bot_name: "Alpha", board_state: "X|.|.\n.|.|.\n.|.|." },
    { move_number: 2, bot_name: "Beta", board_state: "X|.|.\n.|O|.\n.|.|." },
    { move_number: 3, bot_name: "Alpha", board_state: "X|X|.\n.|O|.\n.|.|." },
];


test("initialState starts at index -1 (empty board), not playing", () => {
    const state = initialState(SAMPLE_MOVES);
    assert.equal(state.index, -1);
    assert.equal(state.playing, false);
    assert.deepEqual(state.moves, SAMPLE_MOVES);
});

test("play sets playing=true without changing index", () => {
    const before = initialState(SAMPLE_MOVES);
    const after = nextState(before, { type: "play" });
    assert.equal(after.playing, true);
    assert.equal(after.index, -1);
});

test("pause sets playing=false without changing index", () => {
    const state = { moves: SAMPLE_MOVES, index: 1, playing: true };
    const after = nextState(state, { type: "pause" });
    assert.equal(after.playing, false);
    assert.equal(after.index, 1);
});

test("stepForward advances index by 1", () => {
    const state = initialState(SAMPLE_MOVES);
    const after = nextState(state, { type: "stepForward" });
    assert.equal(after.index, 0);
});

test("stepForward stops at the last move and pauses", () => {
    const state = { moves: SAMPLE_MOVES, index: 2, playing: true };
    const after = nextState(state, { type: "stepForward" });
    assert.equal(after.index, 2);
    assert.equal(after.playing, false);
});

test("stepBack decrements index", () => {
    const state = { moves: SAMPLE_MOVES, index: 2, playing: false };
    const after = nextState(state, { type: "stepBack" });
    assert.equal(after.index, 1);
});

test("stepBack does not go below -1 (empty board)", () => {
    const state = { moves: SAMPLE_MOVES, index: -1, playing: false };
    const after = nextState(state, { type: "stepBack" });
    assert.equal(after.index, -1);
});

test("stepBack pauses playback (manual control overrides auto)", () => {
    const state = { moves: SAMPLE_MOVES, index: 2, playing: true };
    const after = nextState(state, { type: "stepBack" });
    assert.equal(after.playing, false);
});

test("jumpStart returns to empty board, pauses", () => {
    const state = { moves: SAMPLE_MOVES, index: 2, playing: true };
    const after = nextState(state, { type: "jumpStart" });
    assert.equal(after.index, -1);
    assert.equal(after.playing, false);
});

test("jumpEnd lands on last move, pauses", () => {
    const state = initialState(SAMPLE_MOVES);
    const after = nextState(state, { type: "jumpEnd" });
    assert.equal(after.index, 2);
    assert.equal(after.playing, false);
});

test("replay resets index to -1 and starts playing", () => {
    const state = { moves: SAMPLE_MOVES, index: 2, playing: false };
    const after = nextState(state, { type: "replay" });
    assert.equal(after.index, -1);
    assert.equal(after.playing, true);
});

test("tick while playing advances the index", () => {
    const state = { moves: SAMPLE_MOVES, index: 0, playing: true };
    const after = nextState(state, { type: "tick" });
    assert.equal(after.index, 1);
    assert.equal(after.playing, true);
});

test("tick while paused does nothing", () => {
    const state = { moves: SAMPLE_MOVES, index: 0, playing: false };
    const after = nextState(state, { type: "tick" });
    assert.deepEqual(after, state);
});

test("tick at the last move stops auto-play", () => {
    const state = { moves: SAMPLE_MOVES, index: 2, playing: true };
    const after = nextState(state, { type: "tick" });
    assert.equal(after.index, 2);
    assert.equal(after.playing, false);
});

test("unknown action returns state unchanged", () => {
    const state = initialState(SAMPLE_MOVES);
    const after = nextState(state, { type: "bogus" });
    assert.deepEqual(after, state);
});

test("nextState does not mutate its input", () => {
    const state = Object.freeze({ moves: SAMPLE_MOVES, index: 0, playing: false });
    nextState(state, { type: "stepForward" });
    nextState(state, { type: "play" });
    nextState(state, { type: "jumpEnd" });
});
