"""End-to-end browser tests for the animated match-detail player.

The reducer is unit-tested in `tests/js/match-player-state.test.mjs`. This
file covers the DOM adapter (`web/static/match-player.mjs`) — the part
that wires events, schedules timers, and updates cells. Tests run against
a real Chromium driving a live uvicorn-served app via Playwright.
"""

import asyncio
import concurrent.futures
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import (
    TEST_ASYNC_URL,
    db_insert_bot,
    db_insert_match,
    db_insert_move,
)

T = TypeVar("T")


def _sync(coro_factory: Callable[[AsyncEngine], Awaitable[T]]) -> T:
    """Run an async coroutine on its own event loop in a worker thread.

    pytest-asyncio's auto mode already owns the main thread's loop; calling
    `asyncio.run` here would fail with "loop already running". Pushing the
    work into a fresh thread gives it a clean loop to use."""

    async def _run() -> T:
        eng = create_async_engine(TEST_ASYNC_URL)
        try:
            return await coro_factory(eng)
        finally:
            await eng.dispose()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(_run())).result()

# Five-move match ending in X winning across the top.
BOARDS = [
    "X|.|.\n.|.|.\n.|.|.",
    "X|.|.\n.|O|.\n.|.|.",
    "X|X|.\n.|O|.\n.|.|.",
    "X|X|.\n.|O|.\n.|.|O",
    "X|X|X\n.|O|.\n.|.|O",
]


@pytest.fixture
def clean_db() -> None:
    """Truncate test tables before each browser test."""

    async def _truncate(engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE")
            )

    _sync(_truncate)


@pytest.fixture
def x_winning_match(clean_db) -> int:
    async def seed(engine: AsyncEngine) -> int:
        a = await db_insert_bot(engine, "BotA")
        b = await db_insert_bot(engine, "BotB")
        match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
        bots = [a, b, a, b, a]
        for i, board in enumerate(BOARDS, start=1):
            await db_insert_move(engine, match_id, i, bots[i - 1], board)
        return match_id

    return _sync(seed)


@pytest.fixture
def forfeit_match(clean_db) -> int:
    async def seed(engine: AsyncEngine) -> int:
        a = await db_insert_bot(engine, "GoodBot")
        b = await db_insert_bot(engine, "CrashBot")
        match_id = await db_insert_match(
            engine, a, b, winner_id=a, result="o_forfeit"
        )
        await db_insert_move(engine, match_id, 1, a, BOARDS[0])
        await db_insert_move(
            engine, match_id, 2, b, BOARDS[0], error="empty response"
        )
        return match_id

    return _sync(seed)


# Six-move match ending with O winning across the bottom row.
O_WINNING_BOARDS = [
    "X|.|.\n.|.|.\n.|.|.",
    "X|.|.\n.|.|.\nO|.|.",
    "X|X|.\n.|.|.\nO|.|.",
    "X|X|.\n.|.|.\nO|O|.",
    "X|X|.\n.|X|.\nO|O|.",
    "X|X|.\n.|X|.\nO|O|O",
]


@pytest.fixture
def o_winning_match(clean_db) -> int:
    async def seed(engine: AsyncEngine) -> int:
        a = await db_insert_bot(engine, "AlphaBot")
        b = await db_insert_bot(engine, "BetaBot")
        match_id = await db_insert_match(engine, a, b, winner_id=b, result="o_wins")
        bots = [a, b, a, b, a, b]
        for i, board in enumerate(O_WINNING_BOARDS, start=1):
            await db_insert_move(engine, match_id, i, bots[i - 1], board)
        return match_id

    return _sync(seed)


@pytest.fixture
def cat_match(clean_db) -> int:
    """A draw. Move content doesn't matter for the caption test."""

    async def seed(engine: AsyncEngine) -> int:
        a = await db_insert_bot(engine, "AlphaBot")
        b = await db_insert_bot(engine, "BetaBot")
        match_id = await db_insert_match(engine, a, b, winner_id=None, result="cat")
        await db_insert_move(engine, match_id, 1, a, BOARDS[0])
        return match_id

    return _sync(seed)


def _board_text(page: Page) -> str:
    """Read the 9 player-board cells as a single string for assertions."""
    cells = page.locator("#match-player-board .cell")
    expect(cells).to_have_count(9)
    return "".join(cells.all_text_contents())


def test_auto_play_eventually_places_marks(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """The page loads empty and within 5s starts placing marks on its own.
    Catches the regression where the DOM adapter is gutted: without working
    JS, the board stays empty forever."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    assert _board_text(page) == "", "board should start empty"
    expect(page.locator("#match-player-board .cell-x")).to_have_count(
        1, timeout=5_000
    )


def test_jump_to_end_shows_final_board(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Clicking jump-end immediately advances to the final move regardless
    of where playback is, with all marks in place."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-board .cell-x")).to_have_count(3)
    expect(page.locator("#match-player-board .cell-o")).to_have_count(2)


def test_pause_stops_advance(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """After the first move auto-plays, clicking pause stops the next one."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    # Wait for auto-play to place the first X (≈1.4s after load).
    expect(page.locator("#match-player-board .cell-x")).to_have_count(
        1, timeout=3_000
    )
    page.locator('[data-action="playPause"]').click()
    page.wait_for_timeout(2_500)
    # No O should appear in the time it would have taken the next tick.
    expect(page.locator("#match-player-board .cell-o")).to_have_count(0)


def test_step_forward_advances_one_move(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Stepping forward from a paused empty board shows exactly one X."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="playPause"]').click()  # pause
    page.locator('[data-action="stepForward"]').click()
    expect(page.locator("#match-player-board .cell-x")).to_have_count(1)
    expect(page.locator("#match-player-board .cell-o")).to_have_count(0)


def test_result_caption_appears_at_end_of_match(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """After jumping to the last move, the caption announces who won."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-status")).to_contain_text("BotA won")


def test_forfeit_caption_shows_error_text(
    live_server: str, page: Page, forfeit_match: int
) -> None:
    """The caption shows the forfeit reason at the forfeit move."""
    page.goto(f"{live_server}/matches/{forfeit_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-status")).to_contain_text("empty response")


def test_final_board_shows_correct_letters_in_correct_cells(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Counting `.cell-x` / `.cell-o` is not enough — the cell must show
    the letter too. Catches the regression where the class is added but
    `textContent` is never set, leaving colored-but-empty cells."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    # The five-move match ends with this final board (X wins across the top):
    #   X X X
    #   . O .
    #   . . O
    assert _board_text(page) == "XXX" + "O" + "O"


def test_jump_start_clears_marks_from_a_filled_board(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """After jumping to the end and back to the start, the board must be
    fully empty again — no stale `.cell-x`/`.cell-o` classes and no stale
    letter text. Catches the regression where stale classes accumulate
    because the render() never clears them on backward motion."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-board .cell-x")).to_have_count(3)
    page.locator('[data-action="jumpStart"]').click()
    expect(page.locator("#match-player-board .cell-x")).to_have_count(0)
    expect(page.locator("#match-player-board .cell-o")).to_have_count(0)
    assert _board_text(page) == ""


def test_step_back_removes_the_most_recent_mark(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Forward then back must remove the mark that was just placed.
    Catches the regression where `el.classList.remove(...)` is gone and
    classes only accumulate."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="playPause"]').click()  # pause auto-play
    page.locator('[data-action="stepForward"]').click()
    expect(page.locator("#match-player-board .cell-x")).to_have_count(1)
    page.locator('[data-action="stepBack"]').click()
    expect(page.locator("#match-player-board .cell-x")).to_have_count(0)
    assert _board_text(page) == ""


def test_every_cell_has_empty_class_after_jump_start(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """After jumpStart, every cell must have `.cell-empty` — not just the
    *absence* of `.cell-x`/`.cell-o`. Catches the regression where the
    `else el.classList.add("cell-empty")` branch is removed: cells end up
    classless, which breaks the CSS that hides letters on empty cells."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    page.locator('[data-action="jumpStart"]').click()
    expect(page.locator("#match-player-board .cell-empty")).to_have_count(9)


def test_initial_status_caption_is_press_play_to_start(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Caption when the board is empty and not playing. We first let auto-play
    advance so the status changes; then jumpStart should *reset* it back to
    'Press play to start.' via the render() path. Without this preamble the
    HTML's default initial text masks a regression in the JS render branch."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    expect(page.locator("#match-player-status")).to_contain_text(
        "Move", timeout=3_000
    )
    page.locator('[data-action="jumpStart"]').click()
    expect(page.locator("#match-player-status")).to_have_text(
        "Press play to start."
    )


def test_play_pause_state_toggles_with_clicks(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """The play/pause button advertises its current mode via `data-state`.
    Catches the regression where the JS no longer flips the icon between
    playing and paused (now an SVG swap rather than a text glyph)."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    btn = page.locator('[data-action="playPause"]')
    expect(btn).to_have_attribute("data-state", "playing", timeout=2_000)
    btn.click()
    expect(btn).to_have_attribute("data-state", "paused")
    btn.click()
    expect(btn).to_have_attribute("data-state", "playing")


def test_o_wins_caption_shows_o_bot_name(
    live_server: str, page: Page, o_winning_match: int
) -> None:
    """The `o_wins` branch of matchOverCaption — never hit by x_winning_match."""
    page.goto(f"{live_server}/matches/{o_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-status")).to_contain_text("BetaBot won")


def test_cat_game_caption(
    live_server: str, page: Page, cat_match: int
) -> None:
    """The `cat` branch of matchOverCaption."""
    page.goto(f"{live_server}/matches/{cat_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-status")).to_have_text("Cat game")


def test_per_move_caption_uses_exact_format(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Caption between moves is `Move N — BotName`, with the right number
    and name. Catches changes to either the format string or the values
    it interpolates."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="playPause"]').click()  # pause auto-play
    page.locator('[data-action="stepForward"]').click()
    expect(page.locator("#match-player-status")).to_have_text("Move 1 — BotA")
    page.locator('[data-action="stepForward"]').click()
    expect(page.locator("#match-player-status")).to_have_text("Move 2 — BotB")


def test_forfeit_caption_uses_exact_format(
    live_server: str, page: Page, forfeit_match: int
) -> None:
    """Forfeit caption is `BotName forfeited: <error>`. Catches changes to
    either the prefix wording or the punctuation."""
    page.goto(f"{live_server}/matches/{forfeit_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-status")).to_have_text(
        "CrashBot forfeited: empty response"
    )


def test_step_forward_at_last_move_does_not_advance(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Stepping forward when already at the last move is a no-op for the
    board state. The DOM should look identical before and after the click."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    before = _board_text(page)
    page.locator('[data-action="stepForward"]').click()
    assert _board_text(page) == before
    # And mark counts are still the same.
    expect(page.locator("#match-player-board .cell-x")).to_have_count(3)
    expect(page.locator("#match-player-board .cell-o")).to_have_count(2)


def test_step_back_at_start_does_not_advance(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Stepping back when the board is already empty is a no-op."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpStart"]').click()
    page.locator('[data-action="stepBack"]').click()
    expect(page.locator("#match-player-board .cell-empty")).to_have_count(9)
    assert _board_text(page) == ""


def test_a_marked_cell_never_also_has_cell_empty_class(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Mutual-exclusion invariant. CSS gives `.cell-empty { color: transparent }`,
    which overrides X/O color if both classes are present, so a cell would
    show no letter even with the right textContent. This guards the
    `el.classList.remove("cell-empty", ...)` line before adding a new class."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    # No cell should be tagged both as containing a mark AND empty.
    expect(
        page.locator("#match-player-board .cell-x.cell-empty")
    ).to_have_count(0)
    expect(
        page.locator("#match-player-board .cell-o.cell-empty")
    ).to_have_count(0)


def test_caption_shows_bot_about_to_move_when_playing_at_start(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """When state is {index: -1, playing: true}, caption is
    `${xBot} is about to move…`. The auto-play window is brief, so we
    reach the same state via stepBack-from-move-1 + playPause."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    expect(page.locator("#match-player-board .cell-x")).to_have_count(
        1, timeout=3_000
    )
    page.locator('[data-action="stepBack"]').click()  # back to index=-1, paused
    page.locator('[data-action="playPause"]').click()  # now playing from start
    expect(page.locator("#match-player-status")).to_contain_text(
        "BotA is about to move"
    )


def test_second_to_last_move_shows_move_caption_not_result(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Guards the `state.index === state.moves.length - 1` boundary.
    At move 4 of 5 the caption must be the per-move format, not the
    end-of-match result."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    page.locator('[data-action="stepBack"]').click()
    # x_winning_match has 5 moves, so stepping back from end → move 4.
    expect(page.locator("#match-player-status")).to_have_text("Move 4 — BotB")


def test_newly_placed_mark_gets_cell_placing_class_for_animation(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """The fade-in animation hinges on `cell-placing` being added each time
    an empty cell becomes a mark. Catches the regression where the entire
    `if (wasEmpty && becomesMark)` block is removed — marks would still
    appear but without the animation."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="playPause"]').click()  # pause auto-play
    page.locator('[data-action="stepForward"]').click()
    # Top-left cell just transitioned empty → X.
    expect(page.locator("#match-player-board .cell").nth(0)).to_have_class(
        re.compile(r"\bcell-placing\b")
    )


def test_play_pause_button_becomes_replay_at_end_of_match(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """When the match ends, the play/pause button becomes a replay control:
    inline SVG replaces the play glyph and the `replay` class flips on."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    btn = page.locator('[data-action="playPause"]')
    expect(btn).to_have_class(re.compile(r"\breplay\b"))
    expect(btn.locator("svg")).to_be_visible()


def test_clicking_replay_restarts_playback_from_the_beginning(
    live_server: str, page: Page, x_winning_match: int
) -> None:
    """Replay click resets to empty board and resumes auto-advancing."""
    page.goto(f"{live_server}/matches/{x_winning_match}")
    page.locator('[data-action="jumpEnd"]').click()
    expect(page.locator("#match-player-board .cell-x")).to_have_count(3)
    btn = page.locator('[data-action="playPause"]')
    btn.click()  # the replay click
    # Immediately after replay, board is empty and we're playing again.
    expect(btn).to_have_attribute("data-state", "playing")
    expect(page.locator("#match-player-board .cell-x")).to_have_count(0)
    # And auto-play advances within a few seconds.
    expect(page.locator("#match-player-board .cell-x")).to_have_count(
        1, timeout=3_000
    )
