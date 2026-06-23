"""Phase 3: POST /play/turn integration with the bot pod via k8s.

The handler now (instead of using the stub) looks up the pod IP from k8s
and POSTs to the pod. These tests patch the k8s client and the HTTP layer."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from entities.bot.model import Bot
from tests.conftest import db_insert_bot


@pytest.fixture
def play_client(client):
    client.cookies.set("ttt_player_name", "Matt")
    return client


async def _make_ready_bot(engine) -> int:
    bot_id = await db_insert_bot(engine, "AlphaBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(Bot)
            .where(Bot.id == bot_id)
            .values(pod_ready=True, pod_name=f"bot-{bot_id}")
        )
        await session.commit()
    return bot_id


def _resp(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = body
    return resp


# ---------------------------------------------------------------------------
# Happy path — pod IP found, HTTP returns a valid board
# ---------------------------------------------------------------------------


async def test_play_turn_calls_pod_and_returns_new_board(play_client, engine) -> None:
    """The handler looks up the pod IP and forwards the bot's reply."""
    bot_id = await _make_ready_bot(engine)
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.5"
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = pod

    import json

    http_resp = _resp(json.dumps({"board": "X|O|.\n.|.|.\n.|.|."}).encode())

    with (
        patch("web.bot_client.get_core_v1", return_value=core_v1),
        patch("web.bot_client.urlopen", return_value=http_resp),
    ):
        resp = play_client.post(
            "/play/turn",
            json={
                "bot_id": bot_id,
                "bot_symbol": "O",
                "board": "X|.|.\n.|.|.\n.|.|.",
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {"board": "X|O|.\n.|.|.\n.|.|."}


# ---------------------------------------------------------------------------
# Failure modes — each maps to a "Game over: ..." reason in the response
# ---------------------------------------------------------------------------


async def test_play_turn_missing_pod_returns_unavailable_reason(
    play_client, engine
) -> None:
    """Pod was deleted between the GET and the POST → 'Bot is unavailable'."""
    from kubernetes.client.exceptions import ApiException

    bot_id = await _make_ready_bot(engine)
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.side_effect = ApiException(status=404)

    with patch("web.bot_client.get_core_v1", return_value=core_v1):
        resp = play_client.post(
            "/play/turn",
            json={
                "bot_id": bot_id,
                "bot_symbol": "O",
                "board": "X|.|.\n.|.|.\n.|.|.",
            },
        )
    body = resp.json()
    assert resp.status_code == 200
    assert body == {"error": "Bot is unavailable"}


async def test_play_turn_timeout_returns_took_too_long(play_client, engine) -> None:
    bot_id = await _make_ready_bot(engine)
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.5"
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = pod

    with (
        patch("web.bot_client.get_core_v1", return_value=core_v1),
        patch("web.bot_client.urlopen", side_effect=TimeoutError("read timed out")),
    ):
        resp = play_client.post(
            "/play/turn",
            json={
                "bot_id": bot_id,
                "bot_symbol": "O",
                "board": "X|.|.\n.|.|.\n.|.|.",
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {"error": "Bot took too long"}


async def test_play_turn_invalid_response_returns_invalid_move(
    play_client, engine
) -> None:
    bot_id = await _make_ready_bot(engine)
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.5"
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = pod

    bad_resp = _resp(b"not json")

    with (
        patch("web.bot_client.get_core_v1", return_value=core_v1),
        patch("web.bot_client.urlopen", return_value=bad_resp),
    ):
        resp = play_client.post(
            "/play/turn",
            json={
                "bot_id": bot_id,
                "bot_symbol": "O",
                "board": "X|.|.\n.|.|.\n.|.|.",
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {"error": "Bot returned an invalid move"}
