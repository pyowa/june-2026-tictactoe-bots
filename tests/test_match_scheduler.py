"""Unit tests for match_scheduler/main.py — pod-ready consumer."""

from unittest.mock import AsyncMock, MagicMock, patch

import aio_pika

from match_scheduler.main import handle_pod_ready_message
from messaging.contracts import MATCH_ONDECK_QUEUE, MatchOndeck, PodReadyMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(body: bytes) -> MagicMock:
    msg = MagicMock(spec=aio_pika.IncomingMessage)
    msg.body = body
    msg.process = MagicMock(return_value=AsyncMock().__aenter__.return_value)
    msg.ack = AsyncMock()
    msg.process.return_value.__aenter__ = AsyncMock(return_value=None)
    msg.process.return_value.__aexit__ = AsyncMock(return_value=None)
    return msg


def _make_bot_mock(bot_id: int) -> MagicMock:
    bot = MagicMock()
    bot.id = bot_id
    return bot


def _make_session_ctx(ready_bots: list) -> tuple[MagicMock, MagicMock]:
    """Return (session_ctx, bot_repo_instance) with ready_bots pre-wired."""
    session = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    bot_repo_instance = MagicMock()
    bot_repo_instance.ready_bots = AsyncMock(return_value=ready_bots)
    return session_ctx, bot_repo_instance


# ---------------------------------------------------------------------------
# Single bot — self-pair only
# ---------------------------------------------------------------------------


async def test_handle_pod_ready_message_publishes_self_pair() -> None:
    new_bot_id = 5
    bot = _make_bot_mock(new_bot_id)
    msg_body = PodReadyMessage(bot_id=new_bot_id)

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    session_ctx, bot_repo_instance = _make_session_ctx([bot])

    with (
        patch("match_scheduler.main.get_session", return_value=session_ctx),
        patch("match_scheduler.main.BotRepository", return_value=bot_repo_instance),
    ):
        await handle_pod_ready_message(message, channel)

    channel.default_exchange.publish.assert_awaited_once()
    published_msg = channel.default_exchange.publish.call_args[0][0]
    ondeck = MatchOndeck.model_validate_json(published_msg.body)
    assert ondeck.bot_x_id == new_bot_id
    assert ondeck.bot_o_id == new_bot_id


# ---------------------------------------------------------------------------
# Two bots — self-pair + both directions
# ---------------------------------------------------------------------------


async def test_handle_pod_ready_message_publishes_both_directions_for_other_bots() -> (
    None
):
    new_bot_id = 5
    other_bot_id = 3
    new_bot = _make_bot_mock(new_bot_id)
    other_bot = _make_bot_mock(other_bot_id)
    msg_body = PodReadyMessage(bot_id=new_bot_id)

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    session_ctx, bot_repo_instance = _make_session_ctx([new_bot, other_bot])

    with (
        patch("match_scheduler.main.get_session", return_value=session_ctx),
        patch("match_scheduler.main.BotRepository", return_value=bot_repo_instance),
    ):
        await handle_pod_ready_message(message, channel)

    assert channel.default_exchange.publish.await_count == 3

    published_bodies = [
        MatchOndeck.model_validate_json(call[0][0].body)
        for call in channel.default_exchange.publish.call_args_list
    ]
    pairings = {(m.bot_x_id, m.bot_o_id) for m in published_bodies}
    assert (new_bot_id, new_bot_id) in pairings
    assert (new_bot_id, other_bot_id) in pairings
    assert (other_bot_id, new_bot_id) in pairings


# ---------------------------------------------------------------------------
# Correlation IDs are unique
# ---------------------------------------------------------------------------


async def test_handle_pod_ready_message_correlation_ids_are_unique() -> None:
    new_bot_id = 5
    other_bot_id = 3
    new_bot = _make_bot_mock(new_bot_id)
    other_bot = _make_bot_mock(other_bot_id)
    msg_body = PodReadyMessage(bot_id=new_bot_id)

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    session_ctx, bot_repo_instance = _make_session_ctx([new_bot, other_bot])

    with (
        patch("match_scheduler.main.get_session", return_value=session_ctx),
        patch("match_scheduler.main.BotRepository", return_value=bot_repo_instance),
    ):
        await handle_pod_ready_message(message, channel)

    published_bodies = [
        MatchOndeck.model_validate_json(call[0][0].body)
        for call in channel.default_exchange.publish.call_args_list
    ]
    correlation_ids = [m.correlation_id for m in published_bodies]
    assert len(correlation_ids) == len(set(correlation_ids)), (
        "correlation_ids must all be unique"
    )


# ---------------------------------------------------------------------------
# Routing key is always MATCH_ONDECK_QUEUE
# ---------------------------------------------------------------------------


async def test_handle_pod_ready_message_uses_match_ondeck_queue() -> None:
    new_bot_id = 5
    other_bot_id = 3
    new_bot = _make_bot_mock(new_bot_id)
    other_bot = _make_bot_mock(other_bot_id)
    msg_body = PodReadyMessage(bot_id=new_bot_id)

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    session_ctx, bot_repo_instance = _make_session_ctx([new_bot, other_bot])

    with (
        patch("match_scheduler.main.get_session", return_value=session_ctx),
        patch("match_scheduler.main.BotRepository", return_value=bot_repo_instance),
    ):
        await handle_pod_ready_message(message, channel)

    for call in channel.default_exchange.publish.call_args_list:
        kwargs = call[1]
        assert kwargs["routing_key"] == MATCH_ONDECK_QUEUE


# ---------------------------------------------------------------------------
# Invalid JSON — ack silently, nothing published
# ---------------------------------------------------------------------------


async def test_handle_pod_ready_message_invalid_json_acks_silently() -> None:
    message = _make_message(b"not valid json at all")
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    await handle_pod_ready_message(message, channel)

    channel.default_exchange.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# No ready bots — nothing published
# ---------------------------------------------------------------------------


async def test_handle_pod_ready_message_no_ready_bots_publishes_nothing() -> None:
    new_bot_id = 7
    msg_body = PodReadyMessage(bot_id=new_bot_id)

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    session_ctx, bot_repo_instance = _make_session_ctx([])

    with (
        patch("match_scheduler.main.get_session", return_value=session_ctx),
        patch("match_scheduler.main.BotRepository", return_value=bot_repo_instance),
    ):
        await handle_pod_ready_message(message, channel)

    channel.default_exchange.publish.assert_not_awaited()
