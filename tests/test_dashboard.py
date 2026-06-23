"""Tests for the event-mode `/dashboard` page.

Renders a big-font URL banner on top of the live-polling leaderboard.
The `HOST_IP` env var is injected onto the web Deployment by
`make reload-web` (auto-detected from the host's Wi-Fi interface)."""

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import db_insert_bot, db_insert_match


def test_dashboard_returns_200(client) -> None:
    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_dashboard_shows_host_url_when_env_set(client, monkeypatch) -> None:
    """When HOST_IP is set, the dashboard banner shows `http://<ip>:8000`
    in the .dashboard-url element. Catches deleting either the env-var
    read or the template interpolation."""
    monkeypatch.setenv("HOST_IP", "192.168.1.50")
    resp = client.get("/dashboard")
    assert 'class="dashboard-url">http://192.168.1.50:8000</p>' in resp.text


def test_dashboard_shows_not_detected_placeholder_when_env_unset(
    client, monkeypatch
) -> None:
    """When HOST_IP is unset/empty, the banner shows a placeholder + hint."""
    monkeypatch.delenv("HOST_IP", raising=False)
    resp = client.get("/dashboard")
    assert "(not detected)" in resp.text
    assert "make reload-web" in resp.text


def test_dashboard_shows_not_detected_when_env_is_blank(
    client, monkeypatch
) -> None:
    """A literally-empty HOST_IP is treated the same as unset."""
    monkeypatch.setenv("HOST_IP", "")
    resp = client.get("/dashboard")
    assert "(not detected)" in resp.text


async def test_dashboard_includes_leaderboard_rows(client, engine) -> None:
    """The leaderboard table on the dashboard reflects actual matches."""
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/dashboard")
    assert "AlphaBot" in resp.text
    assert "BetaBot" in resp.text


def test_dashboard_empty_leaderboard_state(client) -> None:
    """No bots → friendly empty state, no rows."""
    resp = client.get("/dashboard")
    assert "No bots submitted yet." in resp.text


def test_dashboard_uses_live_poll_for_auto_refresh(client) -> None:
    """The leaderboard table is wrapped in #live-region and the
    `live-poll.js` script is loaded so the page refreshes during events."""
    resp = client.get("/dashboard")
    assert 'id="live-region"' in resp.text
    assert "/static/live-poll.js" in resp.text
    assert 'data-target="live-region"' in resp.text


def test_dashboard_hides_site_header(client) -> None:
    """The dashboard overrides the `header` block to be empty — no logo,
    no site title, no nav. The page is just the IP banner + leaderboard.
    (The `<title>` tag in <head> stays — that's the browser tab name.)"""
    resp = client.get("/dashboard")
    assert "<header>" not in resp.text
    assert "<h1>Pyowa Tic-Tac-Toe Bot Battle</h1>" not in resp.text
    assert "pyowa-logo.png" not in resp.text
    assert "<nav>" not in resp.text


def test_dashboard_hides_cookie_warning_banner(client) -> None:
    """The cookie-warning is inside the header block too, so the dashboard
    suppresses it. (The dashboard doesn't write cookies.)"""
    resp = client.get("/dashboard")
    assert 'id="cookie-warning"' not in resp.text


def test_dashboard_omits_nav_links(client) -> None:
    """None of the regular nav anchors appear on the dashboard — it's a
    display-only mode with no navigation chrome."""
    resp = client.get("/dashboard")
    for label in ("Home", "Submit", "Play", "Matches"):
        # The label might still appear inside a column header etc., so anchor
        # to the link pattern that would only appear in the nav.
        assert f'>{label}</a>' not in resp.text, f"unexpected nav link: {label}"


def test_dashboard_renders_audio_overlay(client) -> None:
    """The 'Click to enable sound' overlay is rendered (initially hidden;
    JS unhides on load). Required because browser autoplay policies block
    the AudioContext until a user gesture."""
    resp = client.get("/dashboard")
    assert 'id="audio-overlay"' in resp.text
    assert "Click to enable sound" in resp.text


def test_dashboard_loads_dashboard_mjs_for_sound(client) -> None:
    """The dashboard.mjs script is loaded so the WebSocket + Web Audio
    plumbing runs."""
    resp = client.get("/dashboard")
    assert 'src="/static/dashboard.mjs"' in resp.text
    # Module type required for `import`-statement support in dashboard.mjs.
    assert 'type="module"' in resp.text


def test_dashboard_has_demo_buttons_for_both_sounds(client) -> None:
    """Two small buttons on the dashboard preview the airhorn and the
    battle-end sound so the host can demo them to the room."""
    resp = client.get("/dashboard")
    assert 'id="demo-bot-uploaded"' in resp.text
    assert "New Bot" in resp.text
    assert 'id="demo-match-finished"' in resp.text
    assert "New Match" in resp.text


# ---------------------------------------------------------------------------
# WebSocket endpoint — /dashboard/ws fans broker events out to dashboard tabs
# ---------------------------------------------------------------------------


class _FakeBrokerMessage:
    """Stand-in for an aio_pika.IncomingMessage with the `.process()` async
    context manager + a `.body` attribute, just enough for the endpoint."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def process(self) -> Any:
        class _Ctx:
            async def __aenter__(self_) -> None:
                return None

            async def __aexit__(self_, *args: Any) -> bool:
                return False

        return _Ctx()


class _FakeQueueIterator:
    """Async context manager that yields a pre-canned list of messages then
    raises StopAsyncIteration — mirrors aio_pika.Queue.iterator() output."""

    def __init__(self, messages: list[_FakeBrokerMessage]) -> None:
        self._messages = list(messages)

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> _FakeBrokerMessage:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def _make_fake_broker(messages: list[bytes]) -> MagicMock:
    """Build a fake aio_pika connection that yields the given message bodies
    when its queue iterator is consumed, then halts."""
    fake_queue = MagicMock()
    fake_queue.bind = AsyncMock()
    fake_queue.iterator = lambda: _FakeQueueIterator(
        [_FakeBrokerMessage(b) for b in messages]
    )

    fake_channel = MagicMock()
    fake_channel.declare_exchange = AsyncMock(return_value=MagicMock())
    fake_channel.declare_queue = AsyncMock(return_value=fake_queue)

    fake_connection = MagicMock(is_closed=False)
    fake_connection.channel = AsyncMock(return_value=fake_channel)
    fake_connection.close = AsyncMock()
    return fake_connection


def test_dashboard_ws_forwards_broker_messages_as_text_frames(client) -> None:
    """A message published to the events fanout is forwarded verbatim over
    the WebSocket as a text frame. Covers the happy path through
    `dashboard_ws`: connect, declare exchange + queue, bind, consume, send."""
    fake_connection = _make_fake_broker([b'{"type":"bot.uploaded"}'])
    with patch(
        "web.main.aio_pika.connect_robust", AsyncMock(return_value=fake_connection)
    ):
        with client.websocket_connect("/dashboard/ws") as ws:
            text = ws.receive_text()
    assert text == '{"type":"bot.uploaded"}'


def test_dashboard_ws_forwards_multiple_messages_in_order(client) -> None:
    """All available messages are forwarded in the order they arrive."""
    fake_connection = _make_fake_broker(
        [b'{"type":"bot.uploaded"}', b'{"type":"match.finished"}']
    )
    with patch(
        "web.main.aio_pika.connect_robust", AsyncMock(return_value=fake_connection)
    ):
        with client.websocket_connect("/dashboard/ws") as ws:
            first = ws.receive_text()
            second = ws.receive_text()
    assert first == '{"type":"bot.uploaded"}'
    assert second == '{"type":"match.finished"}'


def test_dashboard_ws_closes_broker_connection_on_exit(client) -> None:
    """The `finally` block in `dashboard_ws` must close the broker connection
    when the iterator drains. Otherwise we'd leak connections per WS."""
    fake_connection = _make_fake_broker([b'{"type":"bot.uploaded"}'])
    with patch(
        "web.main.aio_pika.connect_robust", AsyncMock(return_value=fake_connection)
    ):
        with client.websocket_connect("/dashboard/ws") as ws:
            ws.receive_text()
    fake_connection.close.assert_awaited()


def test_dashboard_ws_skips_close_when_connection_already_closed(client) -> None:
    """If aio_pika has already torn the connection down (e.g. broker restart)
    we mustn't double-close. The `not connection.is_closed` guard covers it."""
    fake_connection = _make_fake_broker([b'{"type":"bot.uploaded"}'])
    fake_connection.is_closed = True  # simulate already-closed
    with patch(
        "web.main.aio_pika.connect_robust", AsyncMock(return_value=fake_connection)
    ):
        with client.websocket_connect("/dashboard/ws") as ws:
            ws.receive_text()
    fake_connection.close.assert_not_awaited()


# ---------------------------------------------------------------------------
# WS exception paths — call the endpoint directly to simulate sends raising
# WebSocketDisconnect / RuntimeError mid-stream (hard to trigger via the
# TestClient WebSocket, easy to inject when we provide the WebSocket mock).
# ---------------------------------------------------------------------------


async def test_dashboard_ws_returns_when_send_raises_websocket_disconnect() -> None:
    """If the client drops mid-stream, send_text raises WebSocketDisconnect;
    the endpoint returns cleanly and still closes the broker connection."""
    from fastapi import WebSocketDisconnect

    from web.main import dashboard_ws

    fake_ws = MagicMock()
    fake_ws.accept = AsyncMock()
    fake_ws.send_text = AsyncMock(side_effect=WebSocketDisconnect(code=1006))

    fake_connection = _make_fake_broker([b"first", b"second"])
    with patch(
        "web.main.aio_pika.connect_robust", AsyncMock(return_value=fake_connection)
    ):
        await dashboard_ws(fake_ws)

    fake_ws.accept.assert_awaited()
    fake_connection.close.assert_awaited()


async def test_dashboard_ws_returns_when_send_raises_runtime_error() -> None:
    """Starlette raises RuntimeError if you send on an already-closed socket.
    The endpoint must swallow it and close the broker connection."""
    from web.main import dashboard_ws

    fake_ws = MagicMock()
    fake_ws.accept = AsyncMock()
    fake_ws.send_text = AsyncMock(
        side_effect=RuntimeError("WebSocket is not connected.")
    )

    fake_connection = _make_fake_broker([b"first"])
    with patch(
        "web.main.aio_pika.connect_robust", AsyncMock(return_value=fake_connection)
    ):
        await dashboard_ws(fake_ws)

    fake_connection.close.assert_awaited()


async def test_dashboard_ws_handles_disconnect_during_setup() -> None:
    """If the client disconnects before broker setup completes, the outer
    `except WebSocketDisconnect` swallows it; the finally block runs with
    `connection is None`, so we don't try to close anything."""
    from fastapi import WebSocketDisconnect

    from web.main import dashboard_ws

    fake_ws = MagicMock()
    fake_ws.accept = AsyncMock()

    raising_connect = AsyncMock(side_effect=WebSocketDisconnect(code=1006))
    with patch("web.main.aio_pika.connect_robust", raising_connect):
        # Must not raise — the outer handler catches.
        await dashboard_ws(fake_ws)

    fake_ws.accept.assert_awaited()


def test_dashboard_banner_marked_for_full_width_break_out(client) -> None:
    """The banner CSS class is present and the inline-style template hasn't
    been swapped for a card-wrapped layout that would re-constrain it."""
    resp = client.get("/dashboard")
    assert 'class="dashboard-banner"' in resp.text
    # The banner must NOT be wrapped in a `.card` (which has max-width
    # behavior from being inside the 880px <main> column).
    banner_pos = resp.text.index('class="dashboard-banner"')
    preceding = resp.text[max(0, banner_pos - 200) : banner_pos]
    assert 'class="card"' not in preceding


@pytest.fixture(autouse=True)
def _clear_host_ip(monkeypatch) -> None:
    """Default each test to no HOST_IP so the case is explicit per-test."""
    monkeypatch.delenv("HOST_IP", raising=False)
    # Sanity: HOST_IP shouldn't be set by the dev environment by accident.
    assert "HOST_IP" not in os.environ
