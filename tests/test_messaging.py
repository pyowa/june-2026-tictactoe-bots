import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from messaging.routing import pick_python_version, turn_queue_for
from messaging.rpc_client import RpcClient

# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def test_pick_python_version_picks_higher_numerically() -> None:
    assert pick_python_version("3.9", "3.11") == "3.11"
    assert pick_python_version("3.13", "3.11") == "3.13"


def test_pick_python_version_treats_equal_versions_as_equal() -> None:
    assert pick_python_version("3", "3") == "3"


def test_pick_python_version_handles_unparseable_input() -> None:
    # When parsing fails, the inner `parse` returns `()`. Both branches still
    # call max(a, b, key=parse); the unparseable side gets `()`, the other
    # side gets a real tuple. Real tuples sort higher than the empty tuple,
    # so the parseable version always wins — pin that semantic explicitly
    # so a mutation that swaps the fallback can be caught.
    assert pick_python_version("garbage", "3.11") == "3.11"
    assert pick_python_version("3.11", "garbage") == "3.11"


def test_turn_queue_for_strips_dots() -> None:
    assert turn_queue_for("3") == "turn.py3.requests"
    assert turn_queue_for("3.11") == "turn.py311.requests"
    assert turn_queue_for("3.13") == "turn.py313.requests"


# ---------------------------------------------------------------------------
# db.database sync helpers
# ---------------------------------------------------------------------------


def test_sync_url_converts_asyncpg_driver() -> None:
    from db.database import sync_url
    assert sync_url(
        "postgresql+asyncpg://u:p@h/d"
    ) == "postgresql+psycopg2://u:p@h/d"


def test_create_sync_engine_uses_database_url() -> None:
    from db.database import create_sync_engine
    engine = create_sync_engine()
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RpcClient — correlation_id round-trip without a broker
# ---------------------------------------------------------------------------


async def test_rpc_client_resolves_future_on_matching_correlation_id() -> None:
    """Simulate a worker reply: instantiate the client, manually publish a
    request future, then call `_on_reply` with the matching correlation_id
    and assert the call returns the body."""
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    client = RpcClient(channel, reply_queue_name="reply-q")

    async def driver() -> bytes:
        return await client.call("turn.py3.requests", b"request-body", timeout=2.5)

    task = asyncio.create_task(driver())
    # Yield so the call() publishes and registers the future.
    await asyncio.sleep(0)

    # The publish call carries a correlation_id — extract it from the call args.
    published_message = channel.default_exchange.publish.call_args[0][0]
    correlation_id = published_message.correlation_id
    assert correlation_id is not None
    # RabbitMQ wants expiration in milliseconds while RpcClient.call takes
    # seconds; pin the conversion so dropping `* 1000` is caught.
    assert published_message.expiration == 2500

    # Fake an incoming reply with the matching correlation_id.
    fake_reply = MagicMock()
    fake_reply.correlation_id = correlation_id
    fake_reply.body = b"response-body"
    await client._on_reply(fake_reply)

    result = await task
    assert result == b"response-body"


async def test_rpc_client_times_out_when_no_reply() -> None:
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    client = RpcClient(channel, reply_queue_name="reply-q")
    with pytest.raises(TimeoutError):
        await client.call("turn.py3.requests", b"x", timeout=0.05)
    # Timeout cleanup must remove the pending future so the client doesn't
    # leak state across calls.
    assert client._pending == {}


async def test_rpc_client_ignores_replies_with_unknown_correlation_id() -> None:
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    client = RpcClient(channel, reply_queue_name="reply-q")
    fake_reply = MagicMock()
    fake_reply.correlation_id = "nothing-pending"
    fake_reply.body = b"stray"
    # Should be a no-op; no exception, no state change.
    await client._on_reply(fake_reply)


async def test_rpc_client_ignores_late_reply_when_future_already_done() -> None:
    """If a worker reply arrives after the future has been cancelled/completed
    (e.g. just after a timeout fired but before cleanup ran), `_on_reply`
    must not raise `InvalidStateError` by calling set_result on a done future."""
    channel = MagicMock()
    client = RpcClient(channel, reply_queue_name="reply-q")
    loop = asyncio.get_running_loop()
    done_future: asyncio.Future[bytes] = loop.create_future()
    done_future.cancel()  # future is now in the "done" state
    client._pending["late-cid"] = done_future
    fake_reply = MagicMock()
    fake_reply.correlation_id = "late-cid"
    fake_reply.body = b"late"
    # Must not raise.
    await client._on_reply(fake_reply)


async def test_rpc_client_ignores_reply_with_none_correlation_id() -> None:
    """A reply that arrives with no `correlation_id` (e.g. a misrouted message
    from elsewhere on the broker) must be ignored cleanly — not crash."""
    channel = MagicMock()
    client = RpcClient(channel, reply_queue_name="reply-q")
    fake_reply = MagicMock()
    fake_reply.correlation_id = None
    fake_reply.body = b"orphan"
    await client._on_reply(fake_reply)


async def test_rpc_client_create_declares_and_consumes_reply_queue() -> None:
    """`create()` declares an exclusive reply queue and registers our handler
    on it. Verify via mocks."""
    channel = MagicMock()
    reply_queue = MagicMock()
    reply_queue.name = "amq.gen-fake-reply"
    reply_queue.consume = AsyncMock()
    channel.declare_queue = AsyncMock(return_value=reply_queue)

    client = await RpcClient.create(channel)

    channel.declare_queue.assert_awaited_once_with(exclusive=True, auto_delete=True)
    reply_queue.consume.assert_awaited_once()
    assert client._reply_queue_name == "amq.gen-fake-reply"


# ---------------------------------------------------------------------------
# RabbitMQQueue — verify the publish shape without a broker
# ---------------------------------------------------------------------------


async def test_rabbitmq_queue_publishes_match_job_as_json() -> None:
    import aio_pika

    from messaging.queue import MATCHES_QUEUE, MatchJob
    from messaging.rabbitmq import RabbitMQQueue

    queue = RabbitMQQueue("amqp://unused")
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    queue._channel = channel
    queue._connection = MagicMock(is_closed=False)

    await queue.enqueue_match(MatchJob(bot_x_id=1, bot_o_id=2, python_version="3.13"))

    channel.default_exchange.publish.assert_awaited_once()
    args = channel.default_exchange.publish.call_args
    message = args[0][0]
    routing_key = args[1]["routing_key"]
    assert routing_key == MATCHES_QUEUE
    payload = json.loads(message.body)
    assert payload == {"bot_x_id": 1, "bot_o_id": 2, "python_version": "3.13"}
    # Durability + content-type contracts. Both can be silently broken
    # without changing visible behavior locally, so pin them.
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.content_type == "application/json"
