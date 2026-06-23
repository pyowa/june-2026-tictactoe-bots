// DOM adapter for the match-detail playback UI.
//
// Reads moves from <script id="moves-data" type="application/json">, owns
// the setTimeout loop + click handlers, and applies state to the DOM. All
// pure logic lives in match-player-state.mjs (unit-tested via `node --test`).

import { initialState, nextState } from "/static/match-player-state.mjs";

const TICK_MS = 1100; // total per-move dwell while auto-playing

// Inline SVGs for the play/pause/replay states. Using SVG (rather than unicode
// glyphs) keeps the icons at a consistent weight and baseline across browsers.
const SVG_OPEN = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">';
const SVG_CLOSE = "</svg>";
const ICON_PLAY = SVG_OPEN + '<path d="M8 5v14l11-7z"/>' + SVG_CLOSE;
const ICON_PAUSE =
    SVG_OPEN + '<path d="M6 19h4V5H6zm8-14v14h4V5z"/>' + SVG_CLOSE;
const ICON_REPLAY =
    SVG_OPEN +
    '<path d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8' +
    "s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18" +
    "c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35" +
    ' 2.35z"/>' +
    SVG_CLOSE;

function parseBoard(boardStr) {
    return boardStr.split("\n").map((row) => row.split("|"));
}

function emptyBoard() {
    return Array.from({ length: 3 }, () => [".", ".", "."]);
}

function boardAt(state) {
    return state.index < 0
        ? emptyBoard()
        : parseBoard(state.moves[state.index].board_state);
}

function render(state, els) {
    // Paint the 9 cells from the current board.
    const board = boardAt(state);
    let i = 0;
    for (const row of board) {
        for (const cell of row) {
            const el = els.cells[i++];
            const wasEmpty = el.classList.contains("cell-empty");
            const becomesMark = cell === "X" || cell === "O";
            el.classList.remove("cell-empty", "cell-x", "cell-o");
            if (cell === "X") el.classList.add("cell-x");
            else if (cell === "O") el.classList.add("cell-o");
            else el.classList.add("cell-empty");
            el.textContent = cell === "." ? "" : cell;
            if (wasEmpty && becomesMark) {
                el.classList.add("cell-placing");
            }
        }
    }

    // Caption beneath the board.
    if (state.index < 0) {
        els.status.textContent = state.playing
            ? `${els.board.dataset.xBot} is about to move…`
            : "Press play to start.";
    } else {
        const move = state.moves[state.index];
        if (move.error) {
            els.status.textContent = `${move.bot_name} forfeited: ${move.error}`;
        } else {
            const last = state.index === state.moves.length - 1;
            els.status.textContent = last
                ? matchOverCaption(els.board.dataset)
                : `Move ${move.move_number} — ${move.bot_name}`;
        }
    }

    // Play/Pause icon. When stopped at the last move, the button becomes a
    // replay control; clicking it restarts from the beginning. `data-state`
    // exposes the current mode for tests; `.replay` lets CSS bump the
    // replay icon size if needed.
    const atEnd =
        state.index === state.moves.length - 1 && !state.playing;
    const mode = atEnd ? "replay" : state.playing ? "playing" : "paused";
    els.playPause.dataset.state = mode;
    els.playPause.classList.toggle("replay", atEnd);
    els.playPause.innerHTML =
        mode === "replay" ? ICON_REPLAY : mode === "playing" ? ICON_PAUSE : ICON_PLAY;
}

function matchOverCaption({ xBot, oBot, result }) {
    switch (result) {
        case "x_wins": return `${xBot} won`;
        case "o_wins": return `${oBot} won`;
        case "cat":    return "Cat game";
    }
}

function init() {
    const dataNode = document.getElementById("moves-data");
    const board = document.getElementById("match-player-board");
    if (!dataNode || !board) return;

    const moves = JSON.parse(dataNode.textContent);
    if (!moves.length) return;

    const els = {
        board,
        cells: board.querySelectorAll(".cell"),
        status: document.getElementById("match-player-status"),
        playPause: document.querySelector('[data-action="playPause"]'),
    };

    let state = initialState(moves);
    let timer = null;

    function update(action) {
        state = nextState(state, action);
        render(state, els);
        scheduleTick();
    }

    function scheduleTick() {
        if (timer) {
            clearTimeout(timer);
            timer = null;
        }
        if (state.playing) {
            timer = setTimeout(() => update({ type: "tick" }), TICK_MS);
        }
    }

    document.querySelectorAll(".match-player-controls [data-action]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const a = btn.dataset.action;
            if (a === "playPause") {
                const atEnd =
                    state.index === state.moves.length - 1 && !state.playing;
                if (atEnd) {
                    update({ type: "replay" });
                } else {
                    update({ type: state.playing ? "pause" : "play" });
                }
            } else {
                update({ type: a });
            }
        });
    });

    render(state, els);
    // Auto-play after a short delay so the empty board is visible briefly.
    setTimeout(() => update({ type: "play" }), 300);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
