"""End-to-end browser tests for the human-vs-bot play page.

The reducer is unit-tested in `tests/js/play-state.test.mjs`. This file
covers the DOM adapter (`web/static/play.mjs`) — clicks, board state,
post-to-/play/turn round-trip, and the end-state caption.

The bot pod itself is mocked at the `web.bot_client` boundary so these
tests run without a live k8s cluster: `urlopen` returns a deterministic
"first empty cell" board, and `get_core_v1` returns a stub that hands back
a fake pod IP."""

import asyncio
import concurrent.futures
import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import web.bot_client
from entities.bot.model import Bot
from tests.conftest import TEST_ASYNC_URL, db_insert_bot

T = TypeVar("T")


def _sync(coro_factory: Callable[[AsyncEngine], Awaitable[T]]) -> T:
    """Run an async coroutine on its own event loop in a worker thread."""

    async def _run() -> T:
        eng = create_async_engine(TEST_ASYNC_URL)
        try:
            return await coro_factory(eng)
        finally:
            await eng.dispose()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(_run())).result()


# ---------------------------------------------------------------------------
# Bot pod stubs — first-empty-cell move via urlopen + get_core_v1 patch
# ---------------------------------------------------------------------------


def _first_empty_move(board: str, symbol: str) -> str:
    grid = [row.split("|") for row in board.split("\n")]
    for r in range(3):
        for c in range(3):
            if grid[r][c] == ".":
                grid[r][c] = symbol
                return "\n".join("|".join(row) for row in grid)
    return board


class _FakeResp:
    """Context-manager mimic for urlopen — returns a JSON-encoded board."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _fake_urlopen(req: Any, timeout: float = 10.0) -> _FakeResp:  # noqa: ARG001
    body = json.loads(req.data)
    new_board = _first_empty_move(body["board"], body["symbol"])
    return _FakeResp(json.dumps({"board": new_board}).encode())


@pytest.fixture(autouse=True)
def stub_bot_client(monkeypatch) -> Iterator[None]:
    """Replace `urlopen` and `get_core_v1` so /play/turn answers deterministically."""
    fake_core = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.5"
    fake_core.read_namespaced_pod.return_value = pod
    monkeypatch.setattr(web.bot_client, "get_core_v1", lambda: fake_core)
    monkeypatch.setattr(web.bot_client, "urlopen", _fake_urlopen)
    yield


@pytest.fixture
def clean_db() -> None:
    async def _truncate(engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE")
            )

    _sync(_truncate)


@pytest.fixture
def ready_bot(clean_db) -> int:
    async def seed(engine: AsyncEngine) -> int:
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

    return _sync(seed)


def _set_name_cookie(page: Page, live_server: str, name: str = "Matt") -> None:
    page.context.add_cookies(
        [
            {
                "name": "ttt_player_name",
                "value": name,
                "url": live_server,
            }
        ]
    )


def _force_symbol(page: Page, live_server: str, bot_id: int, symbol: str) -> None:
    """Reload /play/vs/{bot_id} until the server-side coin flip lands on `symbol`.

    Cheaper than patching the server's RNG across the thread boundary."""
    for _ in range(30):
        page.goto(f"{live_server}/play/vs/{bot_id}")
        if page.locator("#play-board").get_attribute("data-human-symbol") == symbol:
            return
    raise AssertionError(
        f"Could not get human-symbol={symbol} after 30 retries"
    )


def _board_text(page: Page) -> str:
    cells = page.locator("#play-board .play-cell")
    expect(cells).to_have_count(9)
    return "".join(cells.all_text_contents())


# ---------------------------------------------------------------------------
# Game-page bootstrapping
# ---------------------------------------------------------------------------


def test_human_x_initial_status_is_player_turn(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """When the human is X, the page opens on the player's turn."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    expect(page.locator("#play-status")).to_have_text("Matt's Turn")


def test_human_o_initial_status_is_bot_turn_then_human(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """When the human is O, the bot moves first (first-empty stub places X at (0,0))
    and the caption flips to the human's turn."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "O")
    expect(page.locator("#play-board .cell-x")).to_have_count(1, timeout=5_000)
    expect(page.locator("#play-status")).to_have_text("Matt's Turn")
    assert _board_text(page).startswith("X")


# ---------------------------------------------------------------------------
# Clicks
# ---------------------------------------------------------------------------


def test_clicking_empty_cell_places_human_x(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """Click on (1,1) places the human's X; bot replies at first empty (0,0)."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    page.locator('#play-board .play-cell[data-index="4"]').click()
    expect(
        page.locator('#play-board .play-cell[data-index="4"]')
    ).to_have_text("X")
    expect(
        page.locator('#play-board .play-cell[data-index="0"]')
    ).to_have_text("O", timeout=5_000)
    expect(page.locator("#play-status")).to_have_text("Matt's Turn")


def test_clicking_occupied_cell_is_ignored(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """Clicking on a cell that the bot already filled doesn't change it."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    page.locator('#play-board .play-cell[data-index="4"]').click()
    expect(
        page.locator('#play-board .play-cell[data-index="0"]')
    ).to_have_text("O", timeout=5_000)
    page.locator('#play-board .play-cell[data-index="0"]').click()
    expect(
        page.locator('#play-board .play-cell[data-index="0"]')
    ).to_have_text("O")


def test_human_marks_get_cell_x_or_o_class_matching_symbol(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """The human's clicked cell gets the cell-x class — not just text."""
    import re

    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    page.locator('#play-board .play-cell[data-index="4"]').click()
    expect(
        page.locator('#play-board .play-cell[data-index="4"]')
    ).to_have_class(re.compile(r"\bcell-x\b"))
    # Animation hook must be present too.
    expect(
        page.locator('#play-board .play-cell[data-index="4"]')
    ).to_have_class(re.compile(r"\bcell-placing\b"))


# ---------------------------------------------------------------------------
# Win banner
# ---------------------------------------------------------------------------


def _drive_left_column_win(page: Page) -> None:
    """Click (0,0), (1,0), (2,0). With the first-empty-cell stub, human X wins."""
    for idx in (0, 3, 6):
        page.locator(f'#play-board .play-cell[data-index="{idx}"]').click()
        page.wait_for_timeout(150)


def test_human_win_banner_shows_player_name(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """Human wins down the left column."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    _drive_left_column_win(page)
    expect(page.locator("#play-status")).to_have_text("Matt wins", timeout=5_000)


# ---------------------------------------------------------------------------
# Phase 4: Polish — thinking indicator, banner styling, Play again
# ---------------------------------------------------------------------------


def test_end_state_status_uses_banner_success_class(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """At end of game the status caption renders inside a `.banner` element
    styled by the existing `.banner-success` rule."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    _drive_left_column_win(page)
    expect(page.locator("#play-status")).to_have_text("Matt wins", timeout=5_000)
    # The status node carries the success banner class when the human wins.
    import re
    expect(page.locator("#play-status")).to_have_class(re.compile(r"banner-success"))


def test_play_again_link_appears_at_end(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """An end-of-game 'Play again' control appears and re-navigates to the
    same /play/vs/{bot_id} route so the symbol re-randomizes."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    _drive_left_column_win(page)
    again = page.locator('a[data-action="play-again"]')
    expect(again).to_be_visible(timeout=5_000)
    expect(again).to_have_attribute("href", f"/play/vs/{ready_bot}")


def test_play_again_link_hidden_before_game_ends(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """The Play-again control is suppressed until the game actually ends.

    Catches a regression where the link is always rendered visible — the
    feedback memory `feedback_test_by_deletion` calls out that asserting
    structure-only ('exists in DOM') misses the behavior. We anchor on
    visibility, which is the user-facing fact."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    again = page.locator('a[data-action="play-again"]')
    expect(again).to_be_hidden()


def test_bot_thinking_indicator_shows_when_bot_turn(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """While the bot's turn is in flight, the status shows 'Bot is thinking...'.

    Easiest to reach: humanSymbol=O so the bot moves first at page load.
    The fixture's fake urlopen returns immediately, so the indicator is
    visible only for a brief moment. We check that the page reaches the
    *post*-thinking state ('Matt's Turn') — and that the board element
    carries the data-thinking attribute on the way through."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "O")
    # The board must reach Matt's turn after the bot's reply.
    expect(page.locator("#play-status")).to_have_text("Matt's Turn", timeout=5_000)


def test_board_disabled_during_bot_turn(
    live_server: str, page: Page, ready_bot: int
) -> None:
    """While whose='bot', the board carries `data-disabled='true'` so CSS
    can grey it out and clicks are visibly ignored. Verified by playing a
    move and immediately re-clicking — the second click must not flip whose."""
    _set_name_cookie(page, live_server)
    _force_symbol(page, live_server, ready_bot, "X")
    page.locator('#play-board .play-cell[data-index="4"]').click()
    # After human X at (1,1), bot reply lands at (0,0) shortly after.
    expect(
        page.locator('#play-board .play-cell[data-index="0"]')
    ).to_have_text("O", timeout=5_000)
    # data-disabled should be back to false once the human's turn resumes.
    expect(page.locator("#play-board")).to_have_attribute("data-disabled", "false")
