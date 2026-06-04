import base64
import json
from typing import Any

import pytest
from sqlalchemy import Engine, text

from runner.orchestrator import (
    fetch_bot_sources,
    handle_match_message,
    play_match_rpc,
)
from tests.conftest import TEST_ASYNC_URL, db_insert_bot


class _ScriptedRpc:
    """Fake `RpcCaller` that returns canned response bodies in order.

    Use a list of dicts; each call pops one off the front and returns its
    JSON encoding. Useful for driving `play_match_rpc` through specific
    scripted scenarios without a real broker."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    async def call(
        self, target_queue: str, payload: bytes, timeout: float = 10.0
    ) -> bytes:
        parsed = json.loads(payload)
        self.calls.append((target_queue, parsed, timeout))
        if not self._responses:
            raise AssertionError("no scripted response left")
        return json.dumps(self._responses.pop(0)).encode()


# ---------------------------------------------------------------------------
# play_match_rpc — game-loop behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_match_x_wins_with_row() -> None:
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # X
            {"board": "X|.|.\n.|O|.\n.|.|.", "error": None},  # O
            {"board": "X|X|.\n.|O|.\n.|.|.", "error": None},  # X
            {"board": "X|X|.\n.|O|.\n.|.|O", "error": None},  # O
            {"board": "X|X|X\n.|O|.\n.|.|O", "error": None},  # X wins
        ]
    )
    result = await play_match_rpc(rpc, b"# bot x", b"# bot o", "3")
    assert result.result == "x_wins"
    assert len(result.moves) == 5


@pytest.mark.asyncio
async def test_play_match_cat_game() -> None:
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
    rpc = _ScriptedRpc([{"board": b, "error": None} for b in boards])
    result = await play_match_rpc(rpc, b"", b"", "3")
    assert result.result == "cat"
    assert len(result.moves) == 9


@pytest.mark.asyncio
async def test_play_match_x_forfeits_on_worker_error() -> None:
    rpc = _ScriptedRpc([{"board": None, "error": "timeout after 5s"}])
    result = await play_match_rpc(rpc, b"", b"", "3")
    assert result.result == "x_forfeit"
    assert "timeout after 5s" in (result.moves[-1].error or "")


@pytest.mark.asyncio
async def test_play_match_x_forfeits_on_unparseable_board() -> None:
    rpc = _ScriptedRpc([{"board": "garbage", "error": None}])
    result = await play_match_rpc(rpc, b"", b"", "3")
    assert result.result == "x_forfeit"
    assert "unparseable" in (result.moves[-1].error or "")


@pytest.mark.asyncio
async def test_play_match_o_forfeits_on_invalid_move() -> None:
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # X plays (0,0)
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # O makes no move
        ]
    )
    result = await play_match_rpc(rpc, b"", b"", "3")
    assert result.result == "o_forfeit"


@pytest.mark.asyncio
async def test_play_match_timeout_results_in_forfeit() -> None:
    class _TimeoutRpc:
        async def call(self, target_queue, payload, timeout=10.0):
            raise TimeoutError()

    result = await play_match_rpc(_TimeoutRpc(), b"", b"", "3", timeout=2.0)
    assert result.result == "x_forfeit"
    assert "timeout after 2.0s" in (result.moves[-1].error or "")


@pytest.mark.asyncio
async def test_play_match_routes_to_right_queue_per_python_version() -> None:
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},
            {"board": "X|.|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|O", "error": None},
            {"board": "X|X|X\n.|O|.\n.|.|O", "error": None},
        ]
    )
    await play_match_rpc(rpc, b"", b"", "3.13")
    for queue_name, _, _ in rpc.calls:
        assert queue_name == "turn.py313.requests"


@pytest.mark.asyncio
async def test_play_match_passes_each_bots_source_to_correct_turns() -> None:
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # X turn
            {"board": "X|.|.\n.|O|.\n.|.|.", "error": None},  # O turn
            {"board": "X|X|.\n.|O|.\n.|.|.", "error": None},  # X turn
            {"board": "X|X|.\n.|O|.\n.|.|O", "error": None},  # O turn
            {"board": "X|X|X\n.|O|.\n.|.|O", "error": None},  # X wins
        ]
    )
    await play_match_rpc(rpc, b"SOURCE_X", b"SOURCE_O", "3")
    decoded = [
        base64.b64decode(call[1]["source_b64"]) for call in rpc.calls
    ]
    # X turns at indices 0, 2, 4; O turns at 1, 3.
    assert decoded[0] == b"SOURCE_X"
    assert decoded[2] == b"SOURCE_X"
    assert decoded[4] == b"SOURCE_X"
    assert decoded[1] == b"SOURCE_O"
    assert decoded[3] == b"SOURCE_O"


# ---------------------------------------------------------------------------
# fetch_bot_sources + handle_match_message — DB integration
# ---------------------------------------------------------------------------


@pytest.fixture()
def _bound_db(engine: Engine) -> None:
    """Bind the async DB engine to the test Postgres so handle_match_message
    can fetch + persist via `get_session()`."""
    import db.database as d
    d.reconfigure(TEST_ASYNC_URL)


def _set_source(engine: Engine, bot_id: int, source: bytes) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE bots SET source = :s WHERE id = :id"),
            {"s": source, "id": bot_id},
        )


@pytest.mark.asyncio
async def test_fetch_bot_sources_returns_x_then_o(
    engine: Engine, _bound_db: None
) -> None:
    a = db_insert_bot(engine, "Alpha")
    b = db_insert_bot(engine, "Beta")
    _set_source(engine, a, b"# alpha source")
    _set_source(engine, b, b"# beta source")
    x_source, o_source = await fetch_bot_sources(a, b)
    assert x_source == b"# alpha source"
    assert o_source == b"# beta source"


async def test_handle_match_message_persists_o_winning_result(
    engine: Engine, _bound_db: None
) -> None:
    """Cover the `o_wins` winner-id branch in `record_match`."""
    a = db_insert_bot(engine, "Alpha")
    b = db_insert_bot(engine, "Beta")
    _set_source(engine, a, b"")
    _set_source(engine, b, b"")

    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},
            {"board": "X|.|O\n.|.|.\n.|.|.", "error": None},
            {"board": "X|X|O\n.|.|.\n.|.|.", "error": None},
            {"board": "X|X|O\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|O\nX|O|.\n.|.|.", "error": None},
            {"board": "X|X|O\nX|O|.\nO|.|.", "error": None},  # O wins anti-diag
        ]
    )
    body = json.dumps(
        {"bot_x_id": a, "bot_o_id": b, "python_version": "3"}
    ).encode()
    await handle_match_message(rpc, body)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT result, winner_id FROM matches")).first()
    assert row is not None
    assert row[0] == "o_wins"
    assert row[1] == b


async def test_handle_match_message_persists_cat_result(
    engine: Engine, _bound_db: None
) -> None:
    """Cover the cat-game winner-id branch (winner_id is NULL)."""
    a = db_insert_bot(engine, "Alpha")
    b = db_insert_bot(engine, "Beta")
    _set_source(engine, a, b"")
    _set_source(engine, b, b"")

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
    rpc = _ScriptedRpc([{"board": b, "error": None} for b in boards])
    body = json.dumps(
        {"bot_x_id": a, "bot_o_id": b, "python_version": "3"}
    ).encode()
    await handle_match_message(rpc, body)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT result, winner_id FROM matches")).first()
    assert row is not None
    assert row[0] == "cat"
    assert row[1] is None


async def test_handle_match_message_persists_result(
    engine: Engine, _bound_db: None
) -> None:
    a = db_insert_bot(engine, "Alpha")
    b = db_insert_bot(engine, "Beta")
    _set_source(engine, a, b"")
    _set_source(engine, b, b"")

    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},
            {"board": "X|.|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|O", "error": None},
            {"board": "X|X|X\n.|O|.\n.|.|O", "error": None},
        ]
    )
    body = json.dumps(
        {"bot_x_id": a, "bot_o_id": b, "python_version": "3"}
    ).encode()
    result = await handle_match_message(rpc, body)
    assert result.result == "x_wins"

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT result, winner_id FROM matches")
        ).first()
    assert row is not None
    assert row[0] == "x_wins"
    assert row[1] == a
