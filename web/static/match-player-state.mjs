// Pure reducer for the match-detail playback UI.
//
// State shape: { moves: Move[], index: number, playing: boolean }
//   index === -1   → empty board, before any move plays
//   index >= 0     → the board shown is moves[index].board_state
//   playing        → auto-advance is active; the DOM adapter schedules tick actions
//
// Action types:
//   play | pause | stepForward | stepBack | jumpStart | jumpEnd | tick | replay

export function initialState(moves) {
    return { moves, index: -1, playing: false };
}

export function nextState(state, action) {
    switch (action.type) {
        case "play":
            return { ...state, playing: true };
        case "pause":
            return { ...state, playing: false };
        case "stepForward":
        case "tick": {
            if (!state.playing && action.type === "tick") return state;
            const last = state.moves.length - 1;
            if (state.index >= last) return { ...state, playing: false };
            return { ...state, index: state.index + 1 };
        }
        case "stepBack":
            return {
                ...state,
                index: Math.max(-1, state.index - 1),
                playing: false,
            };
        case "jumpStart":
            return { ...state, index: -1, playing: false };
        case "jumpEnd":
            return { ...state, index: state.moves.length - 1, playing: false };
        case "replay":
            return { ...state, index: -1, playing: true };
        default:
            return state;
    }
}
