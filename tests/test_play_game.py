"""Phase 2: human-vs-bot game page + stubbed turn endpoint.

These tests cover the server-side surface:
- GET /play/vs/{bot_id} renders the game template (200 / 404 / random symbol)
- POST /play/turn returns the bot's next board (stub: first empty cell)

The browser-driven game-loop tests live in tests/browser/test_play.py."""

import secrets
from unittest.mock import patch

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from entities.bot.model import Bot
from tests.conftest import db_insert_bot


async def _make_ready_bot(engine, name: str) -> int:
    bot_id = await db_insert_bot(engine, name)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(Bot).where(Bot.id == bot_id).values(pod_ready=True)
        )
        await session.commit()
    return bot_id


@pytest.fixture
def play_client(client):
    client.cookies.set("ttt_player_name", "Matt")
    return client


# ---------------------------------------------------------------------------
# GET /play/vs/{bot_id}
# ---------------------------------------------------------------------------


async def test_play_vs_returns_200_for_ready_bot(play_client, engine) -> None:
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert resp.status_code == 200


async def test_play_vs_renders_bot_name(play_client, engine) -> None:
    """The page identifies which bot you're playing against."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert "AlphaBot" in resp.text


async def test_play_vs_renders_player_name(play_client, engine) -> None:
    """The page shows the human player's display name from the cookie."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert "Matt" in resp.text


def test_play_vs_404_for_unknown_bot(play_client) -> None:
    resp = play_client.get("/play/vs/99999")
    assert resp.status_code == 404


async def test_play_vs_404_for_not_ready_bot(play_client, engine) -> None:
    """Bots that aren't `pod_ready` are not playable — 404."""
    bot_id = await db_insert_bot(engine, "NotReady")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert resp.status_code == 404


async def test_play_vs_redirects_to_play_when_no_name_cookie(client, engine) -> None:
    """A visitor without a player-name cookie is bounced to /play to set one."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = client.get(f"/play/vs/{bot_id}", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/play"


async def test_play_vs_human_symbol_x_is_reachable(play_client, engine) -> None:
    """When secrets.choice picks X, the template marks the human as X."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    with patch.object(secrets, "choice", return_value="X"):
        resp = play_client.get(f"/play/vs/{bot_id}")
    assert 'data-human-symbol="X"' in resp.text


async def test_play_vs_human_symbol_o_is_reachable(play_client, engine) -> None:
    """When secrets.choice picks O, the template marks the human as O."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    with patch.object(secrets, "choice", return_value="O"):
        resp = play_client.get(f"/play/vs/{bot_id}")
    assert 'data-human-symbol="O"' in resp.text


async def test_play_vs_bakes_in_bot_id(play_client, engine) -> None:
    """The page exposes the bot id as a data attribute so the JS can POST turns."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert f'data-bot-id="{bot_id}"' in resp.text


async def test_play_vs_bakes_in_bot_name(play_client, engine) -> None:
    """The page exposes the bot's display name so the JS can show '<bot>'s Turn'."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert 'data-bot-name="AlphaBot"' in resp.text


async def test_play_vs_bakes_in_player_name(play_client, engine) -> None:
    """The page exposes the human player's name so the JS can show '<player>'s Turn'."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert 'data-player-name="Matt"' in resp.text


async def test_play_vs_includes_board_with_nine_empty_cells(
    play_client, engine
) -> None:
    """The board starts with 9 empty cells ready to be clicked."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert resp.text.count("play-cell cell-empty") == 9


async def test_play_vs_loads_play_mjs(play_client, engine) -> None:
    """The page loads /static/play.mjs which drives the game loop."""
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.get(f"/play/vs/{bot_id}")
    assert "/static/play.mjs" in resp.text


# ---------------------------------------------------------------------------
# POST /play/turn — guard rails (real-bot behavior tested in
# tests/test_play_turn_integration.py)
# ---------------------------------------------------------------------------


async def test_play_turn_404_for_unknown_bot(play_client) -> None:
    """A turn for a non-existent bot returns 404."""
    resp = play_client.post(
        "/play/turn",
        json={
            "bot_id": 99999,
            "bot_symbol": "O",
            "board": "X|.|.\n.|.|.\n.|.|.",
        },
    )
    assert resp.status_code == 404


async def test_play_turn_404_for_not_ready_bot(play_client, engine) -> None:
    """Even if the bot row exists, an un-ready bot can't take a turn."""
    bot_id = await db_insert_bot(engine, "NotReady")
    resp = play_client.post(
        "/play/turn",
        json={
            "bot_id": bot_id,
            "bot_symbol": "O",
            "board": "X|.|.\n.|.|.\n.|.|.",
        },
    )
    assert resp.status_code == 404


async def test_play_turn_404_for_ready_bot_with_no_pod_name(
    play_client, engine
) -> None:
    """pod_ready=True but pod_name=None is an inconsistent state — return 404.

    This shouldn't happen in production (set_pod_ready sets both fields
    together) but matters as a contract: handle_play_turn can dereference
    bot.pod_name safely after the guard."""
    # _make_ready_bot leaves pod_name NULL; this exercises the guard.
    bot_id = await _make_ready_bot(engine, "AlphaBot")
    resp = play_client.post(
        "/play/turn",
        json={
            "bot_id": bot_id,
            "bot_symbol": "O",
            "board": "X|.|.\n.|.|.\n.|.|.",
        },
    )
    assert resp.status_code == 404
