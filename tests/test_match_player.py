"""Tests for the animated match-detail player.

The page now ships two views of the same data: a JSON blob the JS reads
to drive the animation, and a `<noscript>` fallback that lists the moves
statically for visitors with JS disabled. These tests cover both.
"""

import json
import re

from tests.conftest import db_insert_bot, db_insert_match, db_insert_move

BOARD_AFTER_X = "X|.|.\n.|.|.\n.|.|."
BOARD_AFTER_O = "X|.|.\n.|O|.\n.|.|."


# ---------------------------------------------------------------------------
# Inline JSON blob — the data the JS reads
# ---------------------------------------------------------------------------


async def test_match_player_emits_moves_json(client, engine) -> None:
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    await db_insert_move(engine, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    match = re.search(
        r'<script id="moves-data" type="application/json">(.+?)</script>',
        resp.text,
        re.DOTALL,
    )
    assert match is not None, "moves-data script tag not found"
    moves = json.loads(match.group(1))
    assert len(moves) == 2
    assert moves[0]["move_number"] == 1
    assert moves[0]["bot_name"] == "BotA"
    assert moves[0]["board_state"] == BOARD_AFTER_X
    assert moves[1]["bot_name"] == "BotB"


async def test_match_player_moves_json_includes_forfeit_error(client, engine) -> None:
    a = await db_insert_bot(engine, "GoodBot")
    b = await db_insert_bot(engine, "CrashBot")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="o_forfeit")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    await db_insert_move(
        engine, match_id, 2, b, BOARD_AFTER_X, error="empty response"
    )

    resp = client.get(f"/matches/{match_id}")
    match = re.search(
        r'<script id="moves-data" type="application/json">(.+?)</script>',
        resp.text,
        re.DOTALL,
    )
    assert match is not None
    moves = json.loads(match.group(1))
    assert moves[1]["error"] == "empty response"


# ---------------------------------------------------------------------------
# Player UI scaffolding — board placeholder + controls
# ---------------------------------------------------------------------------


async def test_match_player_renders_board_placeholder(client, engine) -> None:
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)

    resp = client.get(f"/matches/{match_id}")
    assert 'id="match-player-board"' in resp.text


async def test_match_player_renders_all_control_buttons(client, engine) -> None:
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)

    resp = client.get(f"/matches/{match_id}")
    for action in ("jumpStart", "stepBack", "playPause", "stepForward", "jumpEnd"):
        assert f'data-action="{action}"' in resp.text, f"{action} button missing"


async def test_match_player_loads_js_module(client, engine) -> None:
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)

    resp = client.get(f"/matches/{match_id}")
    assert 'src="/static/match-player.mjs"' in resp.text
    assert 'type="module"' in resp.text


# ---------------------------------------------------------------------------
# Noscript fallback — old stacked rendering remains for JS-disabled visitors
# ---------------------------------------------------------------------------


async def test_noscript_fallback_lists_every_move(client, engine) -> None:
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    await db_insert_move(engine, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    noscript = re.search(r"<noscript>(.+?)</noscript>", resp.text, re.DOTALL)
    assert noscript is not None, "noscript fallback missing"
    body = noscript.group(1)
    assert "Move 1" in body
    assert "Move 2" in body
    assert "BotA" in body
    assert "BotB" in body
