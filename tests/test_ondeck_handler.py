"""Unit tests for dispatcher/ondeck_handler.py — ondeck-match consumer."""

from unittest.mock import AsyncMock, MagicMock, patch

from dispatcher.ondeck_handler import handle_match_ondeck
from messaging.contracts import MatchOndeck
from runner.engine import MatchResult
from tests.conftest import make_amqp_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot_mock(bot_id: int, pod_name: str | None = "pod-bot") -> MagicMock:
    bot = MagicMock()
    bot.id = bot_id
    bot.pod_name = pod_name
    return bot


def _make_session_ctx() -> MagicMock:
    session = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_handle_match_ondeck_happy_path_records_result() -> None:
    bot_x = _make_bot_mock(1, pod_name="pod-bot-1")
    bot_o = _make_bot_mock(2, pod_name="pod-bot-2")
    msg_body = MatchOndeck(bot_x_id=1, bot_o_id=2, correlation_id="cid-happy")
    message = make_amqp_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    core_v1 = MagicMock()

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={1: bot_x, 2: bot_o})

    match_repo_instance = MagicMock()
    match_repo_instance.record = AsyncMock()

    fake_result = MatchResult("x_wins", [])

    with ( #TODO smell
        patch(
            "dispatcher.ondeck_handler.get_session",
            side_effect=[_make_session_ctx(), _make_session_ctx()],
        ),
        patch(
            "dispatcher.ondeck_handler.BotRepository",
            return_value=bot_repo_instance,
        ),
        patch(
            "dispatcher.ondeck_handler.MatchRepository",
            return_value=match_repo_instance,
        ),
        patch(
            "dispatcher.ondeck_handler.run_match_from_pods",
            return_value=fake_result,
        ),
        patch("asyncio.get_running_loop") as mock_loop,
    ):

        async def fake_run_in_executor(executor, fn, *args):
            return fn()

        mock_loop.return_value.run_in_executor = fake_run_in_executor
        await handle_match_ondeck(message, channel, core_v1)

    match_repo_instance.record.assert_awaited_once()


async def test_handle_match_ondeck_happy_path_match_result_passed_to_record() -> None:
    bot_x = _make_bot_mock(3, pod_name="pod-bot-3")
    bot_o = _make_bot_mock(4, pod_name="pod-bot-4")
    msg_body = MatchOndeck(bot_x_id=3, bot_o_id=4, correlation_id="cid-args")
    message = make_amqp_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    core_v1 = MagicMock()

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={3: bot_x, 4: bot_o})

    match_repo_instance = MagicMock()
    match_repo_instance.record = AsyncMock()

    fake_result = MatchResult("o_wins", [])

    with ( # TODO smell
        patch(
            "dispatcher.ondeck_handler.get_session",
            side_effect=[_make_session_ctx(), _make_session_ctx()],
        ),
        patch(
            "dispatcher.ondeck_handler.BotRepository",
            return_value=bot_repo_instance,
        ),
        patch(
            "dispatcher.ondeck_handler.MatchRepository",
            return_value=match_repo_instance,
        ),
        patch(
            "dispatcher.ondeck_handler.run_match_from_pods",
            return_value=fake_result,
        ),
        patch("asyncio.get_running_loop") as mock_loop,
    ):

        async def fake_run_in_executor(executor, fn, *args):
            return fn()

        mock_loop.return_value.run_in_executor = fake_run_in_executor
        await handle_match_ondeck(message, channel, core_v1)

    match_repo_instance.record.assert_awaited_once_with(3, 4, fake_result, "cid-args")


# ---------------------------------------------------------------------------
# Invalid JSON body — ack silently
# ---------------------------------------------------------------------------


async def test_handle_match_ondeck_invalid_json_acks_silently() -> None:
    message = make_amqp_message(b"not json at all")
    channel = MagicMock()
    core_v1 = MagicMock()

    match_repo_instance = MagicMock()
    match_repo_instance.record = AsyncMock()

    with patch(
        "dispatcher.ondeck_handler.MatchRepository",
        return_value=match_repo_instance,
    ):
        await handle_match_ondeck(message, channel, core_v1)

    match_repo_instance.record.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bot not found — ack silently
# ---------------------------------------------------------------------------


async def test_handle_match_ondeck_bot_not_found_acks_silently() -> None:
    msg_body = MatchOndeck(bot_x_id=999, bot_o_id=998, correlation_id="cid-notfound")
    message = make_amqp_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    core_v1 = MagicMock()

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={})  # neither bot found

    match_repo_instance = MagicMock()
    match_repo_instance.record = AsyncMock()

    with (
        patch(
            "dispatcher.ondeck_handler.get_session",
            return_value=_make_session_ctx(),
        ),
        patch(
            "dispatcher.ondeck_handler.BotRepository",
            return_value=bot_repo_instance,
        ),
        patch(
            "dispatcher.ondeck_handler.MatchRepository",
            return_value=match_repo_instance,
        ),
    ):
        await handle_match_ondeck(message, channel, core_v1)

    match_repo_instance.record.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bot has no pod_name — ack silently
# ---------------------------------------------------------------------------


async def test_handle_match_ondeck_bot_has_no_pod_name_acks_silently() -> None:
    bot_x = _make_bot_mock(5, pod_name=None)  # pod_name is None
    bot_o = _make_bot_mock(6, pod_name="pod-bot-6")
    msg_body = MatchOndeck(bot_x_id=5, bot_o_id=6, correlation_id="cid-nopod")
    message = make_amqp_message(msg_body.model_dump_json().encode())
    channel = MagicMock()
    core_v1 = MagicMock()

    bot_repo_instance = MagicMock()
    bot_repo_instance.by_ids = AsyncMock(return_value={5: bot_x, 6: bot_o})

    match_repo_instance = MagicMock()
    match_repo_instance.record = AsyncMock()

    with (# TODO smell
        patch(
            "dispatcher.ondeck_handler.get_session",
            return_value=_make_session_ctx(),
        ),
        patch(
            "dispatcher.ondeck_handler.BotRepository",
            return_value=bot_repo_instance,
        ),
        patch(
            "dispatcher.ondeck_handler.MatchRepository",
            return_value=match_repo_instance,
        ),
    ):
        await handle_match_ondeck(message, channel, core_v1)

    match_repo_instance.record.assert_not_awaited()
