"""Tests for messaging.health — DB/broker probe factories and FastAPI router."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from messaging.health import (
    broker_check,
    db_check,
    make_health_echo_handler,
    make_health_router,
    worker_echo_check,
)
from tests.conftest import TEST_ASYNC_URL


async def test_db_check_returns_ok_when_select_succeeds() -> None:
    engine = create_async_engine(TEST_ASYNC_URL)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        status, reason = await db_check(factory)()
        assert status == "ok"
        assert reason is None
    finally:
        await engine.dispose()


async def test_db_check_returns_down_when_connection_fails() -> None:
    # Point at a port nothing is listening on so the connect fails fast.
    bad_engine = create_async_engine(
        "postgresql+asyncpg://ttt:ttt@127.0.0.1:1/no_such_db"
    )
    try:
        factory = async_sessionmaker(bad_engine, expire_on_commit=False)
        status, reason = await db_check(factory)()
        assert status == "down"
        assert reason is not None
    finally:
        await bad_engine.dispose()


async def test_broker_check_returns_ok_when_passive_declare_succeeds() -> None:
    channel = MagicMock()
    channel.declare_queue = AsyncMock()
    connection = MagicMock()
    connection.channel = AsyncMock(return_value=channel)
    connection.close = AsyncMock()

    with patch(
        "messaging.health.aio_pika.connect", AsyncMock(return_value=connection)
    ):
        status, reason = await broker_check("amqp://fake", "some_queue")()

    assert status == "ok"
    assert reason is None
    channel.declare_queue.assert_awaited_once_with("some_queue", passive=True)
    connection.close.assert_awaited_once()


async def test_broker_check_returns_down_when_connect_fails() -> None:
    with patch(
        "messaging.health.aio_pika.connect",
        AsyncMock(side_effect=ConnectionError("nope")),
    ):
        status, reason = await broker_check("amqp://fake", "some_queue")()

    assert status == "down"
    assert reason is not None
    assert "nope" in reason


async def test_broker_check_returns_down_when_declare_fails() -> None:
    channel = MagicMock()
    channel.declare_queue = AsyncMock(side_effect=RuntimeError("queue gone"))
    connection = MagicMock()
    connection.channel = AsyncMock(return_value=channel)
    connection.close = AsyncMock()

    with patch(
        "messaging.health.aio_pika.connect", AsyncMock(return_value=connection)
    ):
        status, reason = await broker_check("amqp://fake", "some_queue")()

    assert status == "down"
    assert reason is not None
    assert "queue gone" in reason
    connection.close.assert_awaited_once()


def test_health_router_returns_200_when_all_checks_pass() -> None:
    async def ok():
        return "ok", None

    app = FastAPI()
    app.include_router(make_health_router({"db": ok, "broker": ok}))

    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"db": "ok", "broker": "ok"}


def test_health_router_returns_503_when_any_check_fails() -> None:
    async def ok():
        return "ok", None

    async def down():
        return "down", "kaboom"

    app = FastAPI()
    app.include_router(make_health_router({"db": ok, "broker": down}))

    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json() == {"detail": {"db": "ok", "broker": "down: kaboom"}}


# ---------------------------------------------------------------------------
# Echo handler (runs inside the worker process, replies to /health RPC calls)
# ---------------------------------------------------------------------------


async def test_health_echo_handler_returns_all_ok_when_deps_healthy() -> None:
    engine = create_async_engine(TEST_ASYNC_URL)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        channel = MagicMock()
        channel.declare_queue = AsyncMock()

        handler = make_health_echo_handler(factory, channel, "some.publish.queue")
        reply = await handler(b"my-nonce")

        assert json.loads(reply) == {
            "echo": "my-nonce",
            "db": "ok",
            "publish_queue": "ok",
        }
        channel.declare_queue.assert_awaited_once_with(
            "some.publish.queue", passive=True
        )
    finally:
        await engine.dispose()


async def test_health_echo_handler_reports_db_down_when_db_unreachable() -> None:
    bad = create_async_engine("postgresql+asyncpg://ttt:ttt@127.0.0.1:1/none")
    try:
        factory = async_sessionmaker(bad, expire_on_commit=False)
        channel = MagicMock()
        channel.declare_queue = AsyncMock()

        handler = make_health_echo_handler(factory, channel, "some.queue")
        reply = json.loads(await handler(b"x"))

        assert reply["echo"] == "x"
        assert reply["db"].startswith("down:")
        assert reply["publish_queue"] == "ok"
    finally:
        await bad.dispose()


async def test_health_echo_handler_reports_publish_queue_down_on_declare_fail() -> None:
    engine = create_async_engine(TEST_ASYNC_URL)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        channel = MagicMock()
        channel.declare_queue = AsyncMock(side_effect=RuntimeError("queue gone"))

        handler = make_health_echo_handler(factory, channel, "missing.queue")
        reply = json.loads(await handler(b"x"))

        assert reply["db"] == "ok"
        assert reply["publish_queue"].startswith("down:")
        assert "queue gone" in reply["publish_queue"]
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Worker echo check (runs in the /health gateway; sends RPC to the worker)
# ---------------------------------------------------------------------------


def _mocked_rpc(reply_payload: bytes) -> tuple[MagicMock, MagicMock]:
    """Build mocked RpcClient + connection objects that the patched
    aio_pika.connect / RpcClient.create can return."""
    rpc_client = MagicMock()
    rpc_client.call = AsyncMock(return_value=reply_payload)
    connection = MagicMock()
    connection.channel = AsyncMock(return_value=MagicMock())
    connection.close = AsyncMock()
    return rpc_client, connection


async def test_worker_echo_check_returns_ok_when_reply_all_ok() -> None:
    nonce = "fixed-nonce"
    reply = json.dumps({"echo": nonce, "db": "ok", "publish_queue": "ok"}).encode()
    rpc_client, connection = _mocked_rpc(reply)

    with (
        patch(
            "messaging.health.aio_pika.connect", AsyncMock(return_value=connection)
        ),
        patch("messaging.health.RpcClient.create", AsyncMock(return_value=rpc_client)),
        patch("messaging.health.secrets.token_hex", return_value=nonce),
    ):
        status, reason = await worker_echo_check("amqp://fake", "health.test")()

    assert status == "ok"
    assert reason is None
    rpc_client.call.assert_awaited_once_with(
        "health.test", nonce.encode(), timeout=2.0
    )
    connection.close.assert_awaited_once()


async def test_worker_echo_check_returns_down_on_echo_mismatch() -> None:
    reply = json.dumps(
        {"echo": "wrong-nonce", "db": "ok", "publish_queue": "ok"}
    ).encode()
    rpc_client, connection = _mocked_rpc(reply)

    with (
        patch(
            "messaging.health.aio_pika.connect", AsyncMock(return_value=connection)
        ),
        patch("messaging.health.RpcClient.create", AsyncMock(return_value=rpc_client)),
        patch("messaging.health.secrets.token_hex", return_value="right-nonce"),
    ):
        status, reason = await worker_echo_check("amqp://fake", "health.test")()

    assert status == "down"
    assert reason is not None
    assert "echo mismatch" in reason


async def test_worker_echo_check_returns_down_when_sub_status_down() -> None:
    nonce = "n"
    reply = json.dumps(
        {"echo": nonce, "db": "down: ConnectionError(...)", "publish_queue": "ok"}
    ).encode()
    rpc_client, connection = _mocked_rpc(reply)

    with (
        patch(
            "messaging.health.aio_pika.connect", AsyncMock(return_value=connection)
        ),
        patch("messaging.health.RpcClient.create", AsyncMock(return_value=rpc_client)),
        patch("messaging.health.secrets.token_hex", return_value=nonce),
    ):
        status, reason = await worker_echo_check("amqp://fake", "health.test")()

    assert status == "down"
    assert reason is not None
    assert "db" in reason
    assert "ConnectionError" in reason


async def test_worker_echo_check_returns_down_on_connect_failure() -> None:
    with patch(
        "messaging.health.aio_pika.connect",
        AsyncMock(side_effect=ConnectionError("broker unreachable")),
    ):
        status, reason = await worker_echo_check("amqp://fake", "health.test")()

    assert status == "down"
    assert reason is not None
    assert "broker unreachable" in reason


# ---------------------------------------------------------------------------
# Web /health route is wired
# ---------------------------------------------------------------------------


def test_web_health_endpoint_is_wired_with_db_and_broker_checks(client) -> None:
    """The route exists and reports both `db` and `broker` sub-statuses,
    whether or not the broker happens to be reachable in this env."""
    resp = client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json() if resp.status_code == 200 else resp.json()["detail"]
    assert set(body.keys()) == {"db", "broker"}
