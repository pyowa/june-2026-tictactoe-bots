"""Unit tests for dispatcher/match_runner.py — game loop using warm pods."""

from unittest.mock import MagicMock, patch

from dispatcher.match_runner import (
    _forfeit_label,
    run_match_from_pods,
)

# ---------------------------------------------------------------------------
# _forfeit_label — pure function
# ---------------------------------------------------------------------------


def test_forfeit_label_x() -> None:
    assert _forfeit_label("x") == "x_forfeit"


def test_forfeit_label_o() -> None:
    assert _forfeit_label("o") == "o_forfeit"


# ---------------------------------------------------------------------------
# run_match_from_pods — uses existing pods, no create/delete
# ---------------------------------------------------------------------------


def _make_core_v1() -> MagicMock:
    return MagicMock()


def _patch_pods_from_names(
    request_turn_side_effects: list,
    *,
    pod_ip_x: str = "10.0.0.1",
    pod_ip_o: str = "10.0.0.2",
):
    """Context manager factory that patches get_pod_ip and request_turn."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with (
            patch("dispatcher.match_runner.get_pod_ip") as mock_ip,
            patch("dispatcher.match_runner.request_turn") as mock_turn,
        ):
            mock_ip.side_effect = lambda core_v1, name: (
                pod_ip_x if name == "pod-x" else pod_ip_o
            )
            mock_turn.side_effect = request_turn_side_effects
            yield mock_ip, mock_turn

    return _ctx()


def test_run_match_from_pods_x_wins() -> None:
    core_v1 = _make_core_v1()
    turns = [
        {"board": "X|.|.\n.|.|.\n.|.|."},   # X
        {"board": "X|.|.\n.|O|.\n.|.|."},   # O
        {"board": "X|X|.\n.|O|.\n.|.|."},   # X
        {"board": "X|X|.\n.|O|.\n.|.|O"},   # O
        {"board": "X|X|X\n.|O|.\n.|.|O"},   # X wins
    ]
    with _patch_pods_from_names(turns):
        result = run_match_from_pods(core_v1, "pod-x", "pod-o", "cid-fp-x")
    assert result.result == "x_wins"
    assert len(result.moves) == 5


def test_run_match_from_pods_o_wins() -> None:
    core_v1 = _make_core_v1()
    # O wins middle column: O takes [0][1], [1][1], [2][1]
    # X is forced to play elsewhere without winning
    turns = [
        {"board": "X|.|.\n.|.|.\n.|.|."},      # X move 1: X at [0][0]
        {"board": "X|O|.\n.|.|.\n.|.|."},      # O move 1: O at [0][1]
        {"board": "X|O|X\n.|.|.\n.|.|."},      # X move 2: X at [0][2]
        {"board": "X|O|X\n.|O|.\n.|.|."},      # O move 2: O at [1][1]
        {"board": "X|O|X\n.|O|X\n.|.|."},      # X move 3: X at [1][2] (no win)
        {"board": "X|O|X\n.|O|X\n.|O|."},      # O move 3: O at [2][1] — wins col 1
    ]
    with _patch_pods_from_names(turns):
        result = run_match_from_pods(core_v1, "pod-x", "pod-o", "cid-fp-o")
    assert result.result == "o_wins"


def test_run_match_from_pods_draw() -> None:
    core_v1 = _make_core_v1()
    boards = [
        "X|.|.\n.|.|.\n.|.|.",
        "X|.|.\n.|O|.\n.|.|.",
        "X|.|X\n.|O|.\n.|.|.",
        "X|O|X\n.|O|.\n.|.|.",
        "X|O|X\n.|O|.\n.|.|X",
        "X|O|X\n.|O|.\nO|.|X",
        "X|O|X\nX|O|.\nO|.|X",
        "X|O|X\nX|O|O\nO|.|X",
        "X|O|X\nX|O|O\nO|X|X",
    ]
    turns = [{"board": b} for b in boards]
    with _patch_pods_from_names(turns):
        result = run_match_from_pods(core_v1, "pod-x", "pod-o", "cid-fp-draw")
    assert result.result == "cat"
    assert len(result.moves) == 9


def test_run_match_from_pods_x_forfeits_on_http_error() -> None:
    core_v1 = _make_core_v1()
    from urllib.error import URLError

    turns = [URLError("connection refused")]
    with _patch_pods_from_names(turns):
        result = run_match_from_pods(core_v1, "pod-x", "pod-o", "cid-fp-xe")
    assert result.result == "x_forfeit"
    assert result.moves[-1].error is not None


def test_run_match_from_pods_o_forfeits_on_invalid_board() -> None:
    core_v1 = _make_core_v1()
    turns = [
        {"board": "X|.|.\n.|.|.\n.|.|."},   # X valid
        {"board": "not-a-board"},            # O returns garbage
    ]
    with _patch_pods_from_names(turns):
        result = run_match_from_pods(core_v1, "pod-x", "pod-o", "cid-fp-oe")
    assert result.result == "o_forfeit"
    assert "unparseable" in (result.moves[-1].error or "")


