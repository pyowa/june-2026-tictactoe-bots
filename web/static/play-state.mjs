// Pure logic for the human-vs-bot play page.
//
// State shape:
//   {
//     board: string[][],     // 3x3, cells are "X" | "O" | "."
//     whose: "human" | "bot" | "over",
//     status: string,        // caption shown beneath the board
//     ended: boolean,
//   }
//
// All transitions return a new object; the reducer never mutates its input.

const WINNING_LINES = [
    [[0, 0], [0, 1], [0, 2]],
    [[1, 0], [1, 1], [1, 2]],
    [[2, 0], [2, 1], [2, 2]],
    [[0, 0], [1, 0], [2, 0]],
    [[0, 1], [1, 1], [2, 1]],
    [[0, 2], [1, 2], [2, 2]],
    [[0, 0], [1, 1], [2, 2]],
    [[0, 2], [1, 1], [2, 0]],
];

export function emptyBoard() {
    return Array.from({ length: 3 }, () => [".", ".", "."]);
}

export function boardToStr(board) {
    return board.map((row) => row.join("|")).join("\n");
}

export function parseBoard(str) {
    return str.split("\n").map((row) => row.split("|"));
}

export function checkWinner(board) {
    for (const line of WINNING_LINES) {
        const [a, b, c] = line.map(([r, col]) => board[r][col]);
        if (a !== "." && a === b && b === c) return a; // "X" or "O"
    }
    if (board.every((row) => row.every((cell) => cell !== "."))) return "cat";
    return null;
}

export function initialState({ humanSymbol, playerName, botName }) {
    const whose = humanSymbol === "X" ? "human" : "bot";
    return {
        board: emptyBoard(),
        whose,
        ended: false,
        status: `${whose === "human" ? playerName : botName}'s Turn`,
    };
}

function turnCaption({ playerName, botName }, whose) {
    return `${whose === "human" ? playerName : botName}'s Turn`;
}

function endCaption({ playerName, botName, humanSymbol }, winner) {
    if (winner === "cat") return "Cat game";
    if (winner === humanSymbol) return `${playerName} wins`;
    return `${botName} wins`;
}

export function applyHumanMove(state, ctx, index) {
    if (state.ended || state.whose !== "human") return state;
    const r = Math.floor(index / 3);
    const c = index % 3;
    if (state.board[r][c] !== ".") return state;
    const board = state.board.map((row) => row.slice());
    board[r][c] = ctx.humanSymbol;
    const winner = checkWinner(board);
    if (winner) {
        return {
            board,
            whose: "over",
            ended: true,
            status: endCaption(ctx, winner),
        };
    }
    return {
        board,
        whose: "bot",
        ended: false,
        status: turnCaption(ctx, "bot"),
    };
}

export function applyBotMove(state, ctx, newBoardStr) {
    if (state.ended) return state;
    const board = parseBoard(newBoardStr);
    const winner = checkWinner(board);
    if (winner) {
        return {
            board,
            whose: "over",
            ended: true,
            status: endCaption(ctx, winner),
        };
    }
    return {
        board,
        whose: "human",
        ended: false,
        status: turnCaption(ctx, "human"),
    };
}

export function applyForfeit(state, reason) {
    return {
        board: state.board,
        whose: "over",
        ended: true,
        status: `Game over: ${reason}`,
    };
}
