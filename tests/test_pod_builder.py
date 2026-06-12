"""Unit tests for dispatcher/pod_builder.py — build-pod consumer."""

from unittest.mock import AsyncMock, MagicMock, patch

import aio_pika

from dispatcher.pod_builder import handle_build_pod_message
from messaging.contracts import BuildPodMessage, PodReadyMessage

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


def _make_bot_mock(bot_id: int, source: bytes = b"# bot") -> MagicMock:
    bot = MagicMock()
    bot.id = bot_id
    bot.source = source
    return bot


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_handle_build_pod_message_happy_path_publishes_pod_ready() -> None:
    bot = _make_bot_mock(7, source=b"# my bot")
    msg_body = BuildPodMessage(bot_id=7, runtime_key="python-3.14")

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    core_v1 = MagicMock()

    # Build a session mock
    session = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    # BotRepository.by_ids and set_pod_ready are called on an instance
    # We patch BotRepository so we can control it
    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={7: bot})
    bot_repo_instance.set_pod_ready = AsyncMock()

    with (
        patch("dispatcher.pod_builder.get_session", return_value=session_ctx),
        patch("dispatcher.pod_builder.BotRepository", return_value=bot_repo_instance),
        patch("dispatcher.pod_builder.build_bot_pod_manifest", return_value={}),
        patch("dispatcher.pod_builder.get_pod_ip", return_value="10.0.0.7"),
        patch("dispatcher.pod_builder.wait_for_http_ready"),
        patch("asyncio.get_running_loop") as mock_loop,
    ):
        # Make run_in_executor just call the function synchronously
        async def fake_run_in_executor(executor, fn, *args):
            fn()

        mock_loop.return_value.run_in_executor = fake_run_in_executor
        await handle_build_pod_message(message, channel, core_v1)

    channel.default_exchange.publish.assert_awaited_once()
    published_msg = channel.default_exchange.publish.call_args[0][0]
    body = PodReadyMessage.model_validate_json(published_msg.body)
    assert body.bot_id == 7


async def test_handle_build_pod_message_happy_path_updates_db() -> None:
    bot = _make_bot_mock(7, source=b"# my bot")
    msg_body = BuildPodMessage(bot_id=7, runtime_key="python-3.14")

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    core_v1 = MagicMock()

    session = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={7: bot})
    bot_repo_instance.set_pod_ready = AsyncMock()

    with (
        patch("dispatcher.pod_builder.get_session", return_value=session_ctx),
        patch("dispatcher.pod_builder.BotRepository", return_value=bot_repo_instance),
        patch("dispatcher.pod_builder.build_bot_pod_manifest", return_value={}),
        patch("dispatcher.pod_builder.get_pod_ip", return_value="10.0.0.7"),
        patch("dispatcher.pod_builder.wait_for_http_ready"),
        patch("asyncio.get_running_loop") as mock_loop,
    ):
        async def fake_run_in_executor(executor, fn, *args):
            fn()

        mock_loop.return_value.run_in_executor = fake_run_in_executor
        await handle_build_pod_message(message, channel, core_v1)

    bot_repo_instance.set_pod_ready.assert_awaited_once_with(7, "bot-7")


# ---------------------------------------------------------------------------
# wait_for_pod_ready called before get_pod_ip
# ---------------------------------------------------------------------------


async def test_handle_build_pod_message_waits_for_pod_before_getting_ip() -> None:
    """get_pod_ip must not be called until wait_for_pod_ready succeeds."""
    bot = _make_bot_mock(7, source=b"# my bot")
    msg_body = BuildPodMessage(bot_id=7, runtime_key="python-3.14")

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    core_v1 = MagicMock()

    session = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={7: bot})
    bot_repo_instance.set_pod_ready = AsyncMock()

    call_order: list[str] = []

    def fake_wait_for_pod_ready(
        core_v1: MagicMock, pod_name: str, **kwargs: object
    ) -> None:
        call_order.append("wait_for_pod_ready")

    def fake_get_pod_ip(core_v1: MagicMock, pod_name: str) -> str:
        call_order.append("get_pod_ip")
        return "10.0.0.7"

    with (
        patch("dispatcher.pod_builder.get_session", return_value=session_ctx),
        patch("dispatcher.pod_builder.BotRepository", return_value=bot_repo_instance),
        patch("dispatcher.pod_builder.build_bot_pod_manifest", return_value={}),
        patch(
            "dispatcher.pod_builder.wait_for_pod_ready",
            side_effect=fake_wait_for_pod_ready,
        ),
        patch("dispatcher.pod_builder.get_pod_ip", side_effect=fake_get_pod_ip),
        patch("dispatcher.pod_builder.wait_for_http_ready"),
        patch("asyncio.get_running_loop") as mock_loop,
    ):
        from collections.abc import Callable

        async def fake_run_in_executor(
            executor: object, fn: Callable[[], None], *args: object
        ) -> None:
            fn()

        mock_loop.return_value.run_in_executor = fake_run_in_executor
        await handle_build_pod_message(message, channel, core_v1)

    assert call_order == ["wait_for_pod_ready", "get_pod_ip"]


# ---------------------------------------------------------------------------
# Unknown runtime — ack silently, nothing published
# ---------------------------------------------------------------------------


async def test_handle_build_pod_message_unknown_runtime_acks_silently() -> None:
    msg_body = BuildPodMessage(bot_id=5, runtime_key="cobol-85")

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    core_v1 = MagicMock()

    await handle_build_pod_message(message, channel, core_v1)

    channel.default_exchange.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bot not found — ack silently, nothing published
# ---------------------------------------------------------------------------


async def test_handle_build_pod_message_bot_not_found_acks_silently() -> None:
    msg_body = BuildPodMessage(bot_id=999, runtime_key="python-3.14")

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    core_v1 = MagicMock()

    session = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={})  # bot not found

    with (
        patch("dispatcher.pod_builder.get_session", return_value=session_ctx),
        patch("dispatcher.pod_builder.BotRepository", return_value=bot_repo_instance),
    ):
        await handle_build_pod_message(message, channel, core_v1)

    channel.default_exchange.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# Invalid JSON body — ack silently
# ---------------------------------------------------------------------------


async def test_handle_build_pod_message_invalid_json_acks_silently() -> None:
    message = _make_message(b"not json at all")
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    core_v1 = MagicMock()

    await handle_build_pod_message(message, channel, core_v1)

    channel.default_exchange.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# POD_READY_QUEUE routing key
# ---------------------------------------------------------------------------


async def test_handle_build_pod_message_publishes_to_correct_queue() -> None:
    from messaging.contracts import POD_READY_QUEUE

    bot = _make_bot_mock(3, source=b"# bot")
    msg_body = BuildPodMessage(bot_id=3, runtime_key="python-3.14")

    message = _make_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    core_v1 = MagicMock()

    session = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={3: bot})
    bot_repo_instance.set_pod_ready = AsyncMock()

    with (
        patch("dispatcher.pod_builder.get_session", return_value=session_ctx),
        patch("dispatcher.pod_builder.BotRepository", return_value=bot_repo_instance),
        patch("dispatcher.pod_builder.build_bot_pod_manifest", return_value={}),
        patch("dispatcher.pod_builder.get_pod_ip", return_value="10.0.0.3"),
        patch("dispatcher.pod_builder.wait_for_http_ready"),
        patch("asyncio.get_running_loop") as mock_loop,
    ):
        async def fake_run_in_executor(executor, fn, *args):
            fn()

        mock_loop.return_value.run_in_executor = fake_run_in_executor
        await handle_build_pod_message(message, channel, core_v1)

    kwargs = channel.default_exchange.publish.call_args[1]
    assert kwargs["routing_key"] == POD_READY_QUEUE
