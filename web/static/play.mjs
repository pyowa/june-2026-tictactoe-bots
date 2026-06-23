// DOM adapter for the human-vs-bot play page.
//
// Reads context (bot id, names, symbols) from the #play-board data
// attributes, owns the click handlers, and POSTs to /play/turn when it's
// the bot's turn. All pure logic lives in play-state.mjs.

import {
    applyBotMove,
    applyForfeit,
    applyHumanMove,
    boardToStr,
    initialState,
} from "/static/play-state.mjs";

function readContext(board) {
    return {
        botId: board.dataset.botId,
        botName: board.dataset.botName,
        playerName: board.dataset.playerName,
        humanSymbol: board.dataset.humanSymbol,
        botSymbol: board.dataset.botSymbol,
    };
}

const BANNER_SUCCESS_CLASS = "banner-success";
const BANNER_ERROR_CLASS = "banner-error";

function statusKindForState(state, ctx) {
    if (!state.ended) return null;
    if (state.status.startsWith("Game over:")) return "error";
    if (state.status === "Cat game") return "success";
    if (state.status === `${ctx.playerName} wins`) return "success";
    return "error"; // bot wins
}

function applyStatusStyling(els, state, ctx) {
    els.status.classList.remove(
        BANNER_SUCCESS_CLASS,
        BANNER_ERROR_CLASS,
        "banner",
        "muted",
    );
    const kind = statusKindForState(state, ctx);
    if (kind === "success") {
        els.status.classList.add("banner", BANNER_SUCCESS_CLASS);
    } else if (kind === "error") {
        els.status.classList.add("banner", BANNER_ERROR_CLASS);
    } else {
        els.status.classList.add("muted");
    }
}

function render(state, els, ctx, { thinking } = {}) {
    let i = 0;
    for (const row of state.board) {
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
    els.status.textContent = thinking
        ? `${ctx.botName} is thinking...`
        : state.status;
    applyStatusStyling(els, state, ctx);
    const disabled = state.ended || state.whose === "bot" || thinking;
    els.board.dataset.disabled = disabled ? "true" : "false";
    if (state.ended) {
        els.again.removeAttribute("hidden");
    } else {
        els.again.setAttribute("hidden", "");
    }
}

async function postTurn(ctx, state) {
    const resp = await fetch("/play/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            bot_id: Number(ctx.botId),
            bot_symbol: ctx.botSymbol,
            board: boardToStr(state.board),
        }),
    });
    if (!resp.ok) throw new Error("Bot is unavailable");
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    if (!data.board) throw new Error("Bot returned an invalid move");
    return data.board;
}

function init() {
    const board = document.getElementById("play-board");
    if (!board) return;

    const ctx = readContext(board);
    const els = {
        board,
        cells: board.querySelectorAll(".play-cell"),
        status: document.getElementById("play-status"),
        again: document.getElementById("play-again-link"),
    };

    let state = initialState(ctx);
    render(state, els, ctx);

    async function takeBotTurn() {
        render(state, els, ctx, { thinking: true });
        try {
            const newBoardStr = await postTurn(ctx, state);
            state = applyBotMove(state, ctx, newBoardStr);
        } catch (err) {
            state = applyForfeit(state, err.message);
        }
        render(state, els, ctx);
    }

    els.cells.forEach((cell) => {
        cell.addEventListener("click", () => {
            const index = Number(cell.dataset.index);
            const next = applyHumanMove(state, ctx, index);
            if (next === state) return; // no-op: not your turn or cell occupied
            state = next;
            render(state, els, ctx);
            if (state.whose === "bot" && !state.ended) {
                takeBotTurn();
            }
        });
    });

    if (state.whose === "bot" && !state.ended) {
        takeBotTurn();
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
