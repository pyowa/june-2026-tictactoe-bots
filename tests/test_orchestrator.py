import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from structlog.testing import capture_logs

import runner.orchestrator  # noqa: F401  -- smoke-import the entrypoint module so coverage sees its top-level imports
from db.session import get_session
from entities.bot.model import Bot
from entities.match.model import Match
from entities.match.repository import MatchRepository
from entities.move.model import Move as MoveModel
from entities.move.repository import MoveRepository
from runner.dispatch import fetch_bot_sources, handle_match_message
from runner.engine import MatchResult, Move
from runner.match_loop import play_match_rpc
from tests.conftest import (
    TEST_ASYNC_URL,
    db_insert_bot,
    db_insert_match,
    db_insert_move,
)


async def _read_match_row(engine: AsyncEngine) -> tuple[str, int | None]:
    """Read (result, winner_id) for the single match row in the test DB.

    All call sites in this module insert exactly one match per test, so a
    bare `select(...).one()` is sufficient — no `match_id` filter needed."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (await session.execute(select(Match.result, Match.winner_id))).one()
        return row.result, row.winner_id


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
    result = await play_match_rpc(rpc, b"# bot x", b"# bot o", "3", "test-cid")
    assert result.result == "x_wins"
    assert len(result.moves) == 5


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
    result = await play_match_rpc(rpc, b"", b"", "3", "test-cid")
    assert result.result == "cat"
    assert len(result.moves) == 9


async def test_play_match_x_forfeits_on_worker_error() -> None:
    rpc = _ScriptedRpc([{"board": None, "error": "timeout after 5s"}])
    result = await play_match_rpc(rpc, b"", b"", "3", "test-cid")
    assert result.result == "x_forfeit"
    assert "timeout after 5s" in (result.moves[-1].error or "")


async def test_play_match_x_forfeits_on_unparseable_board() -> None:
    rpc = _ScriptedRpc([{"board": "garbage", "error": None}])
    result = await play_match_rpc(rpc, b"", b"", "3", "test-cid")
    assert result.result == "x_forfeit"
    assert "unparseable" in (result.moves[-1].error or "")


async def test_play_match_forfeit_uses_no_output_fallback_on_empty_response() -> None:
    """Worker returns `{}` — no error, no board. Persisted move's error
    must be exactly `"no output"`, not None / empty string."""

    class _EmptyDictRpc:
        async def call(self, target_queue, payload, timeout=10.0):
            return b"{}"

    result = await play_match_rpc(_EmptyDictRpc(), b"", b"", "3", "test-cid")
    assert result.result == "x_forfeit"
    assert result.moves[-1].error == "no output"


async def test_play_match_o_forfeits_on_invalid_move() -> None:
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # X plays (0,0)
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # O makes no move
        ]
    )
    result = await play_match_rpc(rpc, b"", b"", "3", "test-cid")
    assert result.result == "o_forfeit"


async def test_play_match_timeout_results_in_forfeit() -> None:
    class _TimeoutRpc:
        async def call(self, target_queue, payload, timeout=10.0):
            raise TimeoutError()

    result = await play_match_rpc(_TimeoutRpc(), b"", b"", "3", "test-cid", timeout=2.0)
    assert result.result == "x_forfeit"
    assert "timeout after 2.0s" in (result.moves[-1].error or "")


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
    await play_match_rpc(rpc, b"", b"", "3.13", "test-cid")
    for queue_name, _, _ in rpc.calls:
        assert queue_name == "turn.py313.requests"


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
    await play_match_rpc(rpc, b"SOURCE_X", b"SOURCE_O", "3", "test-cid")
    decoded = [base64.b64decode(call[1]["source_b64"]) for call in rpc.calls]
    # X turns at indices 0, 2, 4; O turns at 1, 3.
    assert decoded[0] == b"SOURCE_X"
    assert decoded[2] == b"SOURCE_X"
    assert decoded[4] == b"SOURCE_X"
    assert decoded[1] == b"SOURCE_O"
    assert decoded[3] == b"SOURCE_O"


def test_bot_forfeit_init_passes_error_to_base_exception() -> None:
    """_BotForfeit must pass error to super().__init__ so str(exc) == error."""
    from runner.match_loop import _BotForfeit

    exc = _BotForfeit("timeout after 5s")
    assert exc.args == ("timeout after 5s",)
    assert str(exc) == "timeout after 5s"


async def test_request_turn_payload_has_exact_key_names() -> None:
    """All five payload keys must be present by exact name."""
    rpc = _ScriptedRpc([{"board": None, "error": "test"}])  # forfeit ends the match
    await play_match_rpc(rpc, b"", b"", "3", "cid-keys")
    payload = rpc.calls[0][1]
    expected = {"symbol", "board", "source_b64", "correlation_id", "move_number"}
    assert set(payload.keys()) == expected


async def test_request_turn_payload_carries_symbol_correlation_id_move_number() -> None:
    """Pin symbol, correlation_id, and move_number values on the first turn."""
    rpc = _ScriptedRpc([{"board": None, "error": "test"}])  # forfeit ends the match
    await play_match_rpc(rpc, b"", b"", "3", "my-cid")
    payload = rpc.calls[0][1]
    assert payload["symbol"] == "X"
    assert payload["correlation_id"] == "my-cid"
    assert payload["move_number"] == 1


async def test_request_turn_forwards_timeout_to_rpc_call() -> None:
    """timeout kwarg must reach rpc.call unchanged."""
    rpc = _ScriptedRpc([{"board": None, "error": "test"}])  # forfeit ends the match
    await play_match_rpc(rpc, b"", b"", "3", "cid", timeout=7.5)
    assert rpc.calls[0][2] == 7.5


async def test_play_match_o_player_recorded_as_lowercase_o() -> None:
    """O moves must record player == "o" — kills O-player label mutants."""
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},
            {"board": "X|.|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|O", "error": None},
            {"board": "X|X|X\n.|O|.\n.|.|O", "error": None},
        ]
    )
    result = await play_match_rpc(rpc, b"", b"", "3", "cid")
    assert result.moves[1].player == "o"
    assert result.moves[3].player == "o"


async def test_play_match_forfeit_move_carries_move_number_player_and_board() -> None:
    """After a forfeit, the last Move must carry move_number, player, and board."""
    rpc = _ScriptedRpc([{"board": None, "error": "timeout after 5s"}])
    result = await play_match_rpc(rpc, b"", b"", "3", "cid")
    assert result.result == "x_forfeit"
    m = result.moves[-1]
    assert m.move_number == 1
    assert m.player == "x"
    assert m.board == ".|.|.\n.|.|.\n.|.|."


async def test_play_match_forfeit_error_for_invalid_move_is_not_none() -> None:
    """validate_move failure must produce a non-None, non-empty error string."""
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # X plays (0,0)
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},  # O makes no move
        ]
    )
    result = await play_match_rpc(rpc, b"", b"", "3", "cid")
    assert result.result == "o_forfeit"
    assert result.moves[-1].error is not None
    assert len(result.moves[-1].error) > 0


# ---------------------------------------------------------------------------
# fetch_bot_sources + handle_match_message — DB integration
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def _bound_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """Bind the async DB engine to the test Postgres so handle_match_message
    can fetch + persist via `get_session()`."""
    import db.session

    db.session.reconfigure(TEST_ASYNC_URL)
    yield


async def _set_source(engine: AsyncEngine, bot_id: int, source: bytes) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        bot = await session.get(Bot, bot_id)
        assert bot is not None, f"no Bot row with id={bot_id}"
        bot.source = source
        await session.commit()


async def test_fetch_bot_sources_returns_x_then_o(
    engine: AsyncEngine, _bound_db: None
) -> None:
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    await _set_source(engine, a, b"# alpha source")
    await _set_source(engine, b, b"# beta source")
    x_source, o_source = await fetch_bot_sources(a, b)
    assert x_source == b"# alpha source"
    assert o_source == b"# beta source"


async def test_handle_match_message_persists_o_winning_result(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """Cover the `o_wins` winner-id branch in `MatchRepository.record`."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    await _set_source(engine, a, b"")
    await _set_source(engine, b, b"")

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
        {
            "bot_x_id": a,
            "bot_o_id": b,
            "python_version": "3",
            "correlation_id": "test-cid",
        }
    ).encode()
    await handle_match_message(rpc, body)
    result_value, winner_id = await _read_match_row(engine)
    assert result_value == "o_wins"
    assert winner_id == b


async def test_handle_match_message_persists_cat_result(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """Cover the cat-game winner-id branch (winner_id is NULL)."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    await _set_source(engine, a, b"")
    await _set_source(engine, b, b"")

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
        {
            "bot_x_id": a,
            "bot_o_id": b,
            "python_version": "3",
            "correlation_id": "test-cid",
        }
    ).encode()
    await handle_match_message(rpc, body)
    result_value, winner_id = await _read_match_row(engine)
    assert result_value == "cat"
    assert winner_id is None


async def test_handle_match_message_persists_result(
    engine: AsyncEngine, _bound_db: None
) -> None:
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    await _set_source(engine, a, b"")
    await _set_source(engine, b, b"")

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
        {
            "bot_x_id": a,
            "bot_o_id": b,
            "python_version": "3",
            "correlation_id": "test-cid",
        }
    ).encode()
    result = await handle_match_message(rpc, body)
    assert result.result == "x_wins"

    result_value, winner_id = await _read_match_row(engine)
    # Each persisted Move row's bot_id must agree with its player symbol:
    # X moves are owned by bot_x, O moves by bot_o. Pair them up rather
    # than just counting rows so a flipped mapping in `MatchRepository.record`
    # is caught.
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        move_rows = (
            await session.execute(
                select(
                    MoveModel.move_number,
                    MoveModel.bot_id,
                    MoveModel.board_state,
                ).order_by(MoveModel.move_number)
            )
        ).all()
    assert result_value == "x_wins"
    assert winner_id == a

    # Odd-numbered moves (1, 3, 5) are X turns; even-numbered (2, 4) are O.
    assert len(move_rows) == 5
    for move_number, bot_id, _board_state in move_rows:
        if move_number % 2 == 1:
            assert bot_id == a, (
                f"move #{move_number} was an X turn but bot_id={bot_id} "
                f"(expected bot_x_id={a})"
            )
        else:
            assert bot_id == b, (
                f"move #{move_number} was an O turn but bot_id={bot_id} "
                f"(expected bot_o_id={b})"
            )


# ---------------------------------------------------------------------------
# MatchRepository.record — forfeit result -> winner_id mapping.
# These call record directly (no broker, no RPC) because the mapping is
# the entire surface under test.
# ---------------------------------------------------------------------------


async def test_record_match_x_forfeit_credits_o_as_winner(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """When X forfeits, the *non*-forfeiting bot (O) must be the winner."""
    bot_x_id = await db_insert_bot(engine, "Alpha")
    bot_o_id = await db_insert_bot(engine, "Beta")

    result = MatchResult(
        result="x_forfeit",
        moves=[Move(1, "x", ".|.|.\n.|.|.\n.|.|.", "timeout after 5s")],
    )
    async with get_session() as session:
        await MatchRepository(session).record(bot_x_id, bot_o_id, result, "test-cid")

    result_value, winner_id = await _read_match_row(engine)
    assert result_value == "x_forfeit"
    assert winner_id == bot_o_id


async def test_record_match_o_forfeit_credits_x_as_winner(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """When O forfeits, the *non*-forfeiting bot (X) must be the winner."""
    bot_x_id = await db_insert_bot(engine, "Alpha")
    bot_o_id = await db_insert_bot(engine, "Beta")

    result = MatchResult(
        result="o_forfeit",
        moves=[
            Move(1, "x", "X|.|.\n.|.|.\n.|.|."),
            Move(2, "o", "X|.|.\n.|.|.\n.|.|.", "no move made"),
        ],
    )
    async with get_session() as session:
        await MatchRepository(session).record(bot_x_id, bot_o_id, result, "test-cid")

    result_value, winner_id = await _read_match_row(engine)
    assert result_value == "o_forfeit"
    assert winner_id == bot_x_id


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


async def test_play_match_logs_turn_request_and_result_per_turn() -> None:
    rpc = _ScriptedRpc(
        [
            {"board": "X|.|.\n.|.|.\n.|.|.", "error": None},
            {"board": "X|.|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|.", "error": None},
            {"board": "X|X|.\n.|O|.\n.|.|O", "error": None},
            {"board": "X|X|X\n.|O|.\n.|.|O", "error": None},
        ]
    )
    with capture_logs() as cap:
        await play_match_rpc(rpc, b"", b"", "3", "corr-123")

    turn_reqs = [e for e in cap if e["event"] == "turn_request"]
    turn_res = [e for e in cap if e["event"] == "turn_result"]
    assert len(turn_reqs) == 5
    assert len(turn_res) == 5
    assert all(e["correlation_id"] == "corr-123" for e in turn_reqs)
    assert turn_reqs[0] == {
        "event": "turn_request",
        "correlation_id": "corr-123",
        "move_number": 1,
        "symbol": "X",
        "queue": "turn.py3.requests",
        "log_level": "info",
    }
    assert turn_res[0] == {
        "event": "turn_result",
        "correlation_id": "corr-123",
        "move_number": 1,
        "outcome": "valid",
        "log_level": "info",
    }
    assert all(e["outcome"] == "valid" for e in turn_res)


async def test_play_match_logs_forfeit_turn_result() -> None:
    rpc = _ScriptedRpc([{"board": None, "error": "timeout after 5s"}])
    with capture_logs() as cap:
        await play_match_rpc(rpc, b"", b"", "3", "corr-456")

    forfeit = [e for e in cap if e.get("outcome") == "forfeit"]
    assert len(forfeit) == 1
    assert forfeit[0]["event"] == "turn_result"
    assert forfeit[0]["correlation_id"] == "corr-456"
    assert forfeit[0]["move_number"] == 1
    assert forfeit[0]["error"] == "timeout after 5s"


async def test_handle_match_message_logs_match_started_and_complete(
    engine: AsyncEngine, _bound_db: None
) -> None:
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    await _set_source(engine, a, b"")
    await _set_source(engine, b, b"")

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
        {
            "bot_x_id": a,
            "bot_o_id": b,
            "python_version": "3",
            "correlation_id": "corr-789",
        }
    ).encode()
    with capture_logs() as cap:
        await handle_match_message(rpc, body)

    started = [e for e in cap if e["event"] == "match_started"]
    complete = [e for e in cap if e["event"] == "match_complete"]
    assert len(started) == 1
    assert started[0]["correlation_id"] == "corr-789"
    assert started[0]["bot_x_id"] == a
    assert len(complete) == 1
    assert complete[0]["result"] == "x_wins"
    assert complete[0]["moves"] == 5


async def test_record_persists_correlation_id(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x_id = await db_insert_bot(engine, "Alpha")
    bot_o_id = await db_insert_bot(engine, "Beta")
    result = MatchResult(
        result="x_wins",
        moves=[Move(1, "x", "X|X|X\n.|.|.\n.|.|.")],
    )
    async with get_session() as session:
        await MatchRepository(session).record(bot_x_id, bot_o_id, result, "stored-cid")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from entities.match.model import Match

        row = (await session.execute(select(Match.correlation_id))).one()
    assert row.correlation_id == "stored-cid"


# ---------------------------------------------------------------------------
# MatchRepository.record — move error field is persisted
# ---------------------------------------------------------------------------


async def test_record_persists_move_error(engine: AsyncEngine, _bound_db: None) -> None:
    """error=move.error must reach the DB row. Dropping or nulling the field
    would make forfeit error messages invisible on the match-detail page."""
    bot_x_id = await db_insert_bot(engine, "Alpha")
    bot_o_id = await db_insert_bot(engine, "Beta")
    result = MatchResult(
        result="x_forfeit",
        moves=[Move(1, "x", ".|.|.\n.|.|.\n.|.|.", "timeout after 5s")],
    )
    async with get_session() as session:
        await MatchRepository(session).record(bot_x_id, bot_o_id, result, "err-cid")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (await session.execute(select(MoveModel.error))).one()
    assert row.error == "timeout after 5s"


# ---------------------------------------------------------------------------
# MatchRepository.list_for_bot — filtering and ordering
# ---------------------------------------------------------------------------


async def test_list_for_bot_includes_matches_where_bot_is_o(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """list_for_bot must include matches where the bot plays as O, not only X.
    Mutations that replace `bo.c.base_name == base_name` with a falsy or
    inverted condition would silently drop O-side matches."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    # Beta is O in this match
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    async with get_session() as session:
        rows = await MatchRepository(session).list_for_bot("Beta")

    assert len(rows) == 1
    assert rows[0].bot_o == "Beta"


async def test_list_for_bot_excludes_unrelated_matches(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """list_for_bot must not return matches that don't involve the bot family."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    c = await db_insert_bot(engine, "Gamma")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")  # Alpha vs Beta
    await db_insert_match(engine, b, c, winner_id=b, result="x_wins")  # Beta vs Gamma

    async with get_session() as session:
        rows = await MatchRepository(session).list_for_bot("Alpha")

    # Only the Alpha vs Beta match should appear
    assert len(rows) == 1
    assert rows[0].bot_x == "Alpha"


async def test_list_for_bot_returns_newest_first(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """list_for_bot must order by played_at DESC. Dropping the order_by clause
    lets Postgres return rows in any order, breaking the UI listing."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    early = await db_insert_match(
        engine,
        a,
        b,
        winner_id=a,
        result="x_wins",
        played_at="2024-01-01 00:00:00",
    )
    late = await db_insert_match(
        engine,
        a,
        b,
        winner_id=b,
        result="o_wins",
        played_at="2025-06-01 00:00:00",
    )

    async with get_session() as session:
        rows = await MatchRepository(session).list_for_bot("Alpha")

    assert rows[0].id == late
    assert rows[1].id == early


# ---------------------------------------------------------------------------
# _match_select — winner column label and join condition
# ---------------------------------------------------------------------------


async def test_match_select_winner_column_is_named_winner(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """The 'winner' label on bw.c.versioned_name must survive intact.
    Dropping the column, nulling it, renaming it, or inverting the outerjoin
    condition would cause row.winner to be missing or wrong."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    async with get_session() as session:
        rows = await MatchRepository(session).list_all()

    assert len(rows) == 1
    assert rows[0].winner == "Alpha"


async def test_match_select_winner_is_none_for_draw(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """Outerjoin: when there is no winner, row.winner must be None (not an error)."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    await db_insert_match(engine, a, b, winner_id=None, result="cat")

    async with get_session() as session:
        rows = await MatchRepository(session).list_all()

    assert rows[0].winner is None


# ---------------------------------------------------------------------------
# MoveRepository.for_match — ordering and join
# ---------------------------------------------------------------------------


async def test_move_repository_for_match_returns_moves_in_order(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """for_match must ORDER BY move_number ASC. Inserting out-of-order and
    relying on DB order would make the rendered move log scrambled."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    # Insert move 2 before move 1 to expose any missing ORDER BY
    await db_insert_move(engine, match_id, 2, b, "X|.|.\n.|O|.\n.|.|.")
    await db_insert_move(engine, match_id, 1, a, "X|.|.\n.|.|.\n.|.|.")

    async with get_session() as session:
        rows = await MoveRepository(session).for_match(match_id)

    assert [r.move_number for r in rows] == [1, 2]


async def test_move_repository_for_match_projects_bot_name(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """for_match must join Bots and project versioned_name as 'bot_name'.
    A broken join condition (None or dropped) would produce wrong or missing names."""
    a = await db_insert_bot(engine, "Alpha")
    b = await db_insert_bot(engine, "Beta")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, "X|.|.\n.|.|.\n.|.|.")

    async with get_session() as session:
        rows = await MoveRepository(session).for_match(match_id)

    assert rows[0].bot_name == "Alpha"
