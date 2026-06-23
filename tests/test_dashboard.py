"""Tests for the event-mode `/dashboard` page.

Renders a big-font URL banner on top of the live-polling leaderboard.
The `HOST_IP` env var is injected onto the web Deployment by
`make reload-web` (auto-detected from the host's Wi-Fi interface)."""

import os

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
