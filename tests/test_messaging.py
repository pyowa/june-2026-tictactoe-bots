import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from messaging.log import configure_logging
from messaging.routing import pick_python_version, turn_queue_for
from messaging.rpc_client import RpcClient

# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_installs_json_renderer() -> None:
    import structlog

    # Reset to structlog defaults before calling so that any mutation that
    # drops processors= or logger_factory= leaves them at their defaults
    # (empty list / StreamLoggerFactory), which differ from what we assert.
    structlog.reset_defaults()
    configure_logging()
    config = structlog.get_config()
    processor_types = [type(p).__name__ for p in config["processors"]]
    assert "JSONRenderer" in processor_types
    assert "TimeStamper" in processor_types
    # logger_factory must be a PrintLoggerFactory
    assert type(config["logger_factory"]).__name__ == "PrintLoggerFactory"
    # TimeStamper must use ISO format so timestamps are parseable
    timestamper = next(
        p for p in config["processors"] if type(p).__name__ == "TimeStamper"
    )
    assert timestamper.fmt == "iso"


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
# db.session — expire_on_commit contract
# ---------------------------------------------------------------------------


async def test_session_keeps_objects_valid_after_commit(engine) -> None:
    """Pin the `expire_on_commit=False` contract: ORM objects loaded in an
    async session must remain readable after `await session.commit()` without
    an explicit refresh.

    Why this matters: with the SQLAlchemy default (`expire_on_commit=True`),
    attribute access after commit triggers an automatic refresh — which in
    async mode raises `MissingGreenlet` unless the caller awaits a
    `session.refresh(obj)` first. The flag protects future code authors from
    writing post-commit attribute reads that look fine locally but break
    under load. Without this test, flipping the flag back to `True` is
    silently undetected (no current code path exercises post-commit reads)."""
    import db.session
    from entities.bot.model import Bot
    from tests.conftest import TEST_ASYNC_URL

    db.session.reconfigure(TEST_ASYNC_URL)

    async with db.session.get_session() as session:
        bot = Bot(
            base_name="ExpireProbe",
            versioned_name="ExpireProbeV1",
            version=1,
            owner_token="t",
            python_version="3",
            source=b"",
        )
        session.add(bot)
        await session.commit()
        # Post-commit attribute access. With expire_on_commit=True this
        # would trigger an implicit refresh and raise.
        assert bot.base_name == "ExpireProbe"


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
    call_args = channel.default_exchange.publish.call_args
    published_message = call_args[0][0]
    routing_key = call_args[1]["routing_key"]
    correlation_id = published_message.correlation_id
    assert correlation_id is not None
    # routing_key must match the requested queue name
    assert routing_key == "turn.py3.requests"
    # reply_to must be set so the worker knows where to send the response
    assert published_message.reply_to == "reply-q"
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


async def test_rpc_client_call_default_timeout_is_10_seconds() -> None:
    """The default timeout must be 10.0s — expiration sent to broker is 10000ms."""
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    client = RpcClient(channel, reply_queue_name="reply-q")

    async def driver() -> bytes:
        return await client.call("q", b"x")  # no explicit timeout → uses default

    task = asyncio.create_task(driver())
    await asyncio.sleep(0)

    published_message = channel.default_exchange.publish.call_args[0][0]
    assert published_message.expiration == 10000  # 10.0 * 1000

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, TimeoutError):
        pass


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

    await queue.enqueue_match(
        MatchJob(
            bot_x_id=1, bot_o_id=2, python_version="3.13", correlation_id="test-cid"
        )  # noqa: E501
    )

    channel.default_exchange.publish.assert_awaited_once()
    args = channel.default_exchange.publish.call_args
    message = args[0][0]
    routing_key = args[1]["routing_key"]
    assert routing_key == MATCHES_QUEUE
    payload = json.loads(message.body)
    assert payload == {
        "bot_x_id": 1,
        "bot_o_id": 2,
        "python_version": "3.13",
        "correlation_id": "test-cid",
    }
    # Durability + content-type contracts. Both can be silently broken
    # without changing visible behavior locally, so pin them.
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.content_type == "application/json"


# ---------------------------------------------------------------------------
# make_queue — URL forwarded to RabbitMQQueue
# ---------------------------------------------------------------------------


def test_make_queue_uses_configured_broker_url() -> None:
    """make_queue() must pass BROKER_URL to RabbitMQQueue — not None."""
    from messaging.client import BROKER_URL, make_queue

    queue = make_queue()
    assert queue._url == BROKER_URL


# ---------------------------------------------------------------------------
# RpcClient — default timeout is exactly 10.0 seconds
# ---------------------------------------------------------------------------


def test_rpc_client_call_signature_default_timeout_is_10() -> None:
    """The default timeout in `call` must be exactly 10.0.
    Changing it to 11.0 or any other value would silently alter the
    RabbitMQ message expiration sent to the broker."""
    sig = inspect.signature(RpcClient.call)
    assert sig.parameters["timeout"].default == 10.0


# ---------------------------------------------------------------------------
# RabbitMQQueue — __init__ and _ensure_connected
# ---------------------------------------------------------------------------


def test_rabbitmq_queue_init_sets_channel_to_none() -> None:
    """__init__ must set _channel to None so the lazy-connect branch can tell
    that no connection exists yet. Setting it to "" or any truthy value would
    skip the connect branch on the first publish."""
    from messaging.rabbitmq import RabbitMQQueue

    queue = RabbitMQQueue("amqp://test")
    assert queue._channel is None
    assert queue._connection is None
    assert queue._url == "amqp://test"


async def test_ensure_connected_opens_connection_when_none() -> None:
    """_ensure_connected must call aio_pika.connect_robust when _connection is
    None. Changing `or` to `and` in the condition would skip the branch when
    _connection is None and is_closed would raise AttributeError on NoneType."""
    from messaging.rabbitmq import RabbitMQQueue

    mock_channel = MagicMock()
    mock_channel.declare_queue = AsyncMock()
    mock_connection = MagicMock(is_closed=False)
    mock_connection.channel = AsyncMock(return_value=mock_channel)

    queue = RabbitMQQueue("amqp://test")
    with patch(
        "messaging.rabbitmq.aio_pika.connect_robust",
        AsyncMock(return_value=mock_connection),
    ) as mock_connect:
        channel = await queue._ensure_connected()

    from messaging.queue import MATCHES_QUEUE

    mock_connect.assert_awaited_once_with("amqp://test")
    mock_channel.declare_queue.assert_awaited_once_with(MATCHES_QUEUE, durable=True)
    assert channel is mock_channel
    assert queue._connection is mock_connection


# ---------------------------------------------------------------------------
# RabbitMQQueue — close() guards
# ---------------------------------------------------------------------------


async def test_rabbitmq_queue_close_closes_open_connection() -> None:
    """`close()` must call `connection.close()` when connection is open."""
    from messaging.rabbitmq import RabbitMQQueue

    queue = RabbitMQQueue("amqp://test")
    mock_conn = MagicMock(is_closed=False)
    mock_conn.close = AsyncMock()
    queue._connection = mock_conn
    await queue.close()
    mock_conn.close.assert_awaited_once()


async def test_rabbitmq_queue_close_skips_already_closed_connection() -> None:
    """`close()` must not call `connection.close()` when `is_closed` is True."""
    from messaging.rabbitmq import RabbitMQQueue

    queue = RabbitMQQueue("amqp://test")
    mock_conn = MagicMock(is_closed=True)
    mock_conn.close = AsyncMock()
    queue._connection = mock_conn
    await queue.close()
    mock_conn.close.assert_not_awaited()


async def test_rabbitmq_queue_close_skips_when_no_connection() -> None:
    """`close()` must be a no-op when `_connection` is None."""
    from messaging.rabbitmq import RabbitMQQueue

    queue = RabbitMQQueue("amqp://test")
    await queue.close()  # must not raise
