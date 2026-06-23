"""Phase 1: human-vs-bot play foundation — name cookie + bot list page."""

import html

import pytest

from tests.conftest import db_insert_bot

# ---------------------------------------------------------------------------
# Nav link
# ---------------------------------------------------------------------------


def test_nav_includes_play_link(client) -> None:
    """The site-wide nav has a Play link between Submit and Leaderboard."""
    resp = client.get("/")
    text = resp.text
    submit_pos = text.index('href="/submit"')
    play_pos = text.index('href="/play"')
    leaderboard_pos = text.index('href="/leaderboard"')
    assert submit_pos < play_pos < leaderboard_pos


def test_nav_play_link_text_is_play(client) -> None:
    """The Play nav link's anchor text is the word 'Play'."""
    resp = client.get("/")
    assert '<a href="/play">Play</a>' in resp.text


# ---------------------------------------------------------------------------
# GET /play — anonymous vs cookie'd visitor
# ---------------------------------------------------------------------------


def test_play_without_name_cookie_returns_200(client) -> None:
    resp = client.get("/play")
    assert resp.status_code == 200


def test_play_without_name_cookie_renders_name_form(client) -> None:
    """When no `ttt_player_name` cookie is set, the page asks for a name."""
    resp = client.get("/play")
    assert '<form method="post" action="/play/name"' in resp.text
    assert 'name="player_name"' in resp.text


def test_play_without_name_cookie_does_not_render_bot_table(client) -> None:
    """The bot table is suppressed until a name is known."""
    resp = client.get("/play")
    assert 'id="play-bot-table"' not in resp.text


def test_play_with_name_cookie_does_not_render_name_form(client) -> None:
    """When `ttt_player_name` is set, the page shows the bot list, not the form."""
    client.cookies.set("ttt_player_name", "Matt")
    resp = client.get("/play")
    assert 'action="/play/name"' not in resp.text


def test_play_with_name_cookie_greets_player(client) -> None:
    """The greeting on the play page includes the player's name verbatim."""
    client.cookies.set("ttt_player_name", "Matt")
    resp = client.get("/play")
    assert "Matt" in resp.text


# ---------------------------------------------------------------------------
# POST /play/name — sets cookie, redirects/returns the play page
# ---------------------------------------------------------------------------


def test_post_name_sets_cookie(client) -> None:
    """POST /play/name sets the `ttt_player_name` cookie."""
    resp = client.post("/play/name", data={"player_name": "Matt"})
    set_cookie = resp.headers.get("set-cookie", "")
    assert "ttt_player_name=Matt" in set_cookie


def test_post_name_cookie_is_httponly_samesite_lax(client) -> None:
    """The name cookie must be HttpOnly and SameSite=lax, like the ownership cookie."""
    resp = client.post("/play/name", data={"player_name": "Matt"})
    set_cookie = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


def test_post_name_then_get_play_uses_cookie(client) -> None:
    """After POST /play/name, GET /play uses the cookie and skips the form."""
    client.post("/play/name", data={"player_name": "Matt"})
    resp = client.get("/play")
    assert 'action="/play/name"' not in resp.text
    assert "Matt" in resp.text


def test_post_name_strips_whitespace(client) -> None:
    """Surrounding whitespace on the submitted name is stripped before being stored."""
    resp = client.post("/play/name", data={"player_name": "  Matt  "})
    set_cookie = resp.headers.get("set-cookie", "")
    assert "ttt_player_name=Matt" in set_cookie
    # No leading/trailing whitespace in the stored value.
    assert "ttt_player_name=%20" not in set_cookie
    assert "ttt_player_name=+" not in set_cookie


def test_post_name_empty_rejected(client) -> None:
    """Empty / whitespace-only names are rejected with an error and no cookie set."""
    resp = client.post("/play/name", data={"player_name": "   "})
    set_cookie = resp.headers.get("set-cookie", "")
    assert "ttt_player_name=" not in set_cookie
    text = html.unescape(resp.text)
    assert "name" in text.lower()


def test_post_name_html_escaped_in_page(client) -> None:
    """A name containing HTML is escaped when displayed, not rendered as markup."""
    client.cookies.set("ttt_player_name", "<script>alert(1)</script>")
    resp = client.get("/play")
    assert "<script>alert(1)</script>" not in resp.text


# ---------------------------------------------------------------------------
# Bot list — only ready bots, all versions
# ---------------------------------------------------------------------------


@pytest.fixture
def play_client(client):
    """A client that's already past the name-form step."""
    client.cookies.set("ttt_player_name", "Matt")
    return client


async def test_bot_list_shows_only_pod_ready_bots(play_client, engine) -> None:
    """Bots with `pod_ready = False` must not appear in the bot list."""
    await db_insert_bot(engine, "ReadyBot")
    await db_insert_bot(engine, "PendingBot")
    # Flip ReadyBot to ready.
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(Bot).where(Bot.versioned_name == "ReadyBot").values(pod_ready=True)
        )
        await session.commit()

    resp = play_client.get("/play")
    assert "ReadyBot" in resp.text
    assert "PendingBot" not in resp.text


async def test_bot_list_links_to_play_vs_route(play_client, engine) -> None:
    """Each bot row links to /play/vs/{bot_id} so clicking starts a game."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot

    bot_id = await db_insert_bot(engine, "ReadyBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(Bot).where(Bot.id == bot_id).values(pod_ready=True)
        )
        await session.commit()

    resp = play_client.get("/play")
    assert f'href="/play/vs/{bot_id}"' in resp.text


async def test_bot_list_includes_every_ready_version(play_client, engine) -> None:
    """All ready versions appear — no version filtering."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot

    await db_insert_bot(engine, "MyBot", version=1, versioned_name="MyBot")
    await db_insert_bot(engine, "MyBot", version=2, versioned_name="MyBotV2")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(update(Bot).values(pod_ready=True))
        await session.commit()

    resp = play_client.get("/play")
    assert "MyBotV2" in resp.text
    # The v1 row uses the base name "MyBot"; anchor on the link href so the
    # check isn't satisfied by the substring of "MyBotV2".
    assert 'href="/play/vs/1"' in resp.text
    assert 'href="/play/vs/2"' in resp.text


async def test_bot_list_empty_state(play_client) -> None:
    """No ready bots → friendly empty-state message, no rows."""
    resp = play_client.get("/play")
    assert "No bots are ready" in resp.text
    assert 'id="play-bot-table"' not in resp.text


async def test_bot_table_renders_when_ready_bots_exist(play_client, engine) -> None:
    """The `<table id="play-bot-table">` element appears once a ready bot exists.
    Guards against the picker regressing to an unstyled list."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot

    await db_insert_bot(engine, "ReadyBot", python_version="3.13")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(update(Bot).values(pod_ready=True))
        await session.commit()

    resp = play_client.get("/play")
    assert 'id="play-bot-table"' in resp.text


async def test_bot_table_shows_python_version_for_each_row(play_client, engine) -> None:
    """The Python version column displays the bot's python_version field.
    Catches deleting the column from the template."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot

    await db_insert_bot(engine, "ReadyBot", python_version="3.13")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(update(Bot).values(pod_ready=True))
        await session.commit()

    resp = play_client.get("/play")
    assert "<th>Python</th>" in resp.text
    assert "3.13" in resp.text


async def test_bot_table_shows_submitted_date_for_each_row(play_client, engine) -> None:
    """The Submitted column displays a date for each row in YYYY-MM-DD form."""
    import re

    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot

    await db_insert_bot(engine, "ReadyBot", submitted_at="2026-04-15T12:00:00")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(update(Bot).values(pod_ready=True))
        await session.commit()

    resp = play_client.get("/play")
    assert "<th>Submitted</th>" in resp.text
    assert re.search(r"\b2026-04-15\b", resp.text), "submitted date not in response"


async def test_bot_table_play_action_button_per_row(play_client, engine) -> None:
    """Each row has a distinct, styled `Play` action button (not just the name
    link). Catches removing the action column / merging it with the name."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot

    bot_id = await db_insert_bot(engine, "ReadyBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(Bot).where(Bot.id == bot_id).values(pod_ready=True)
        )
        await session.commit()

    resp = play_client.get("/play")
    assert f'class="play-action" href="/play/vs/{bot_id}">Play</a>' in resp.text


# ---------------------------------------------------------------------------
# Repository — ready_bots_for_play returns shape the template needs
# ---------------------------------------------------------------------------


async def test_ready_bots_for_play_returns_only_ready(engine) -> None:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot
    from entities.bot.repository import BotRepository

    ready_id = await db_insert_bot(engine, "ReadyBot")
    await db_insert_bot(engine, "PendingBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(Bot).where(Bot.id == ready_id).values(pod_ready=True)
        )
        await session.commit()
        rows = await BotRepository(session).ready_bots_for_play()

    names = [r.versioned_name for r in rows]
    assert names == ["ReadyBot"]


async def test_ready_bots_for_play_has_id_and_versioned_name(engine) -> None:
    """Query returns id + versioned_name for link building and labels."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot
    from entities.bot.repository import BotRepository

    bot_id = await db_insert_bot(engine, "ReadyBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(Bot).where(Bot.id == bot_id).values(pod_ready=True)
        )
        await session.commit()
        rows = await BotRepository(session).ready_bots_for_play()

    assert rows[0].id == bot_id
    assert rows[0].versioned_name == "ReadyBot"


async def test_ready_bots_for_play_orders_by_versioned_name(engine) -> None:
    """Bots are listed alphabetically so the picker is predictable."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from entities.bot.model import Bot
    from entities.bot.repository import BotRepository

    await db_insert_bot(engine, "Zeta")
    await db_insert_bot(engine, "Alpha")
    await db_insert_bot(engine, "Mike")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(update(Bot).values(pod_ready=True))
        await session.commit()
        rows = await BotRepository(session).ready_bots_for_play()

    assert [r.versioned_name for r in rows] == ["Alpha", "Mike", "Zeta"]
