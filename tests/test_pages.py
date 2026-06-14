import pytest
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from entities.bot.repository import BotRepository
from tests.conftest import (
    db_insert_bot,
    db_insert_match,
    db_insert_move,
    upload,
)


async def _leaderboard(engine: AsyncEngine) -> dict:
    """Call BotRepository.leaderboard() and return rows keyed by base_name."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        rows = await BotRepository(session).leaderboard()
    return {r.base_name: r for r in rows}


BOARD_START = ".|.|.\n.|.|.\n.|.|."
BOARD_AFTER_X = "X|.|.\n.|.|.\n.|.|."
BOARD_AFTER_O = "X|.|.\n.|O|.\n.|.|."


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


def test_leaderboard_returns_200(client):
    resp = client.get("/leaderboard")
    assert resp.status_code == 200


def test_leaderboard_empty_state(client):
    resp = client.get("/leaderboard")
    assert "No bots submitted yet" in resp.text


def test_leaderboard_shows_submitted_bots(client):
    upload(client, "Hal")
    resp = client.get("/leaderboard")
    assert "Hal" in resp.text


async def test_leaderboard_orders_by_wins_descending(client, engine):
    a = await db_insert_bot(engine, "LowBot")
    b = await db_insert_bot(engine, "HighBot")
    await db_insert_match(engine, a, b, winner_id=b, result="o_wins")
    await db_insert_match(engine, a, b, winner_id=b, result="o_wins")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    # HighBot (2 wins) must appear before LowBot (1 win)
    assert text.index("HighBot") < text.index("LowBot")


async def test_leaderboard_tie_broken_by_earlier_submission(client, engine):
    early = await db_insert_bot(engine, "EarlyBot", submitted_at="2024-01-01 00:00:00")
    late = await db_insert_bot(engine, "LateBot", submitted_at="2024-06-01 00:00:00")
    # Give each one win so they're tied
    await db_insert_match(engine, early, late, winner_id=early, result="x_wins")
    await db_insert_match(engine, late, early, winner_id=late, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    assert text.index("EarlyBot") < text.index("LateBot")


async def test_leaderboard_clean_win_count_is_correct(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    bot_a_pos = text.index("BotA")
    row_section = text[bot_a_pos : bot_a_pos + 300]
    assert ">3<" in row_section


async def test_leaderboard_forfeit_win_shown_separately(client, engine):
    a = await db_insert_bot(engine, "GoodBot")
    b = await db_insert_bot(engine, "CrashBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_match(engine, a, b, winner_id=a, result="o_forfeit")

    resp = client.get("/leaderboard")
    soup = BeautifulSoup(resp.text, "html.parser")
    row = soup.find("tr", {"data-bot": "GoodBot"})
    assert row is not None
    cells = row.find_all("td")
    # 1 clean win and 1 forfeit win, each in their own cell
    assert cells[2].text == "1"
    assert cells[3].text == "1"


async def test_leaderboard_forfeit_win_ranks_above_zero_wins(client, engine):
    a = await db_insert_bot(engine, "GoodBot")
    b = await db_insert_bot(engine, "CrashBot")
    await db_insert_match(engine, a, b, winner_id=a, result="o_forfeit")

    resp = client.get("/leaderboard")
    text = resp.text
    assert text.index("GoodBot") < text.index("CrashBot")


async def test_leaderboard_draw_not_counted_as_win(client, engine):
    a = await db_insert_bot(engine, "DrawBot")
    b = await db_insert_bot(engine, "OtherBot")
    await db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get("/leaderboard")
    text = resp.text
    draw_pos = text.index("DrawBot")
    row_section = text[draw_pos : draw_pos + 300]
    assert ">0<" in row_section


# ---------------------------------------------------------------------------
# Leaderboard — repository-level count assertions (mutation-killing)
#
# The HTML-level tests above verify ordering and rough rendering. These tests
# call BotRepository.leaderboard() directly and assert exact column values so
# that mutations to individual WHERE-clause literals and OR branches are caught.
# ---------------------------------------------------------------------------


async def test_leaderboard_clean_wins_counts_x_wins_and_o_wins(engine) -> None:
    """clean_wins.in_(('x_wins', 'o_wins')) — dropping either literal silently
    misses one win type. WinBot wins once as X, once as O → clean_wins == 2.
    LoseBot never wins → clean_wins == 0 (kills winner_id == lb_id → !=)."""
    winner = await db_insert_bot(engine, "WinBot")
    loser = await db_insert_bot(engine, "LoseBot")
    await db_insert_match(engine, winner, loser, winner_id=winner, result="x_wins")
    await db_insert_match(engine, loser, winner, winner_id=winner, result="o_wins")

    lb = await _leaderboard(engine)
    assert lb["WinBot"].clean_wins == 2
    assert lb["LoseBot"].clean_wins == 0


async def test_leaderboard_forfeit_wins_counts_x_forfeit(engine) -> None:
    """forfeit_wins.in_(('x_forfeit', 'o_forfeit')) — dropping 'x_forfeit'
    misses wins where the opponent forfeited as X."""
    winner = await db_insert_bot(engine, "WinBot")
    forfeiter = await db_insert_bot(engine, "ForfeitBot")
    # forfeiter plays X and forfeits → winner (O) gets the forfeit win
    await db_insert_match(
        engine, forfeiter, winner, winner_id=winner, result="x_forfeit"
    )

    lb = await _leaderboard(engine)
    assert lb["WinBot"].forfeit_wins == 1
    assert lb["ForfeitBot"].forfeit_wins == 0


async def test_leaderboard_forfeit_wins_counts_o_forfeit(engine) -> None:
    """forfeit_wins.in_(('x_forfeit', 'o_forfeit')) — dropping 'o_forfeit'
    misses wins where the opponent forfeited as O."""
    winner = await db_insert_bot(engine, "WinBot")
    forfeiter = await db_insert_bot(engine, "ForfeitBot")
    # forfeiter plays O and forfeits → winner (X) gets the forfeit win
    await db_insert_match(
        engine, winner, forfeiter, winner_id=winner, result="o_forfeit"
    )

    lb = await _leaderboard(engine)
    assert lb["WinBot"].forfeit_wins == 1
    assert lb["ForfeitBot"].forfeit_wins == 0


async def test_leaderboard_draws_counted_as_x_and_as_o(engine) -> None:
    """draws or_(bot_x_id == lb_id, bot_o_id == lb_id) — dropping either branch
    misses draws where the family plays the other role. DrawBot draws once as X
    and once as O → draws == 2."""
    draw_bot = await db_insert_bot(engine, "DrawBot")
    other_a = await db_insert_bot(engine, "OtherA")
    other_b = await db_insert_bot(engine, "OtherB")
    await db_insert_match(engine, draw_bot, other_a, winner_id=None, result="cat")
    await db_insert_match(engine, other_b, draw_bot, winner_id=None, result="cat")

    lb = await _leaderboard(engine)
    assert lb["DrawBot"].draws == 2


async def test_leaderboard_draws_excludes_wins(engine) -> None:
    """draws result == 'cat' can flip to != — a clean win must not appear as a
    draw."""
    a = await db_insert_bot(engine, "WinBot")
    b = await db_insert_bot(engine, "LoseBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    lb = await _leaderboard(engine)
    assert lb["WinBot"].draws == 0
    assert lb["LoseBot"].draws == 0


async def test_leaderboard_losses_counted_as_x_and_as_o(engine) -> None:
    """losses or_(bot_x_id == lb_id, bot_o_id == lb_id) — dropping either branch
    misses losses where the family plays the other role. LoseBot loses once as X
    and once as O → losses == 2."""
    lose_bot = await db_insert_bot(engine, "LoseBot")
    win_a = await db_insert_bot(engine, "WinA")
    win_b = await db_insert_bot(engine, "WinB")
    await db_insert_match(engine, lose_bot, win_a, winner_id=win_a, result="o_wins")
    await db_insert_match(engine, win_b, lose_bot, winner_id=win_b, result="x_wins")

    lb = await _leaderboard(engine)
    assert lb["LoseBot"].losses == 2


async def test_leaderboard_losses_excludes_draws(engine) -> None:
    """losses result != 'cat' can flip to == — a draw must not count as a loss."""
    a = await db_insert_bot(engine, "DrawBot")
    b = await db_insert_bot(engine, "OtherBot")
    await db_insert_match(engine, a, b, winner_id=None, result="cat")

    lb = await _leaderboard(engine)
    assert lb["DrawBot"].losses == 0
    assert lb["OtherBot"].losses == 0


async def test_leaderboard_losses_counts_forfeit_loss(engine) -> None:
    """losses or_(winner_id.is_(None), winner_id != lb_id) — a forfeit where
    the opponent is recorded as winner must count as a loss (winner_id != lb_id
    branch). Also verifies the winner's losses stay at 0."""
    loser = await db_insert_bot(engine, "LoseBot")
    winner = await db_insert_bot(engine, "WinBot")
    # loser forfeits as X; winner (O) is recorded as winner_id
    await db_insert_match(engine, loser, winner, winner_id=winner, result="x_forfeit")

    lb = await _leaderboard(engine)
    assert lb["LoseBot"].losses == 1
    assert lb["WinBot"].losses == 0


async def test_leaderboard_lifetime_wins_counts_all_versions(engine) -> None:
    """lifetime_wins bw.base_name == lb_base — mutating to != would credit the
    wrong family. FooV1 and FooV2 each win one match vs External → lifetime_wins
    == 2 for Foo, 0 for External."""
    foo_v1 = await db_insert_bot(engine, "Foo", version=1, versioned_name="Foo")
    foo_v2 = await db_insert_bot(engine, "Foo", version=2, versioned_name="FooV2")
    ext = await db_insert_bot(engine, "External")
    await db_insert_match(engine, foo_v1, ext, winner_id=foo_v1, result="x_wins")
    await db_insert_match(engine, ext, foo_v2, winner_id=foo_v2, result="o_wins")

    lb = await _leaderboard(engine)
    assert lb["Foo"].lifetime_wins == 2
    assert lb["External"].lifetime_wins == 0


async def test_leaderboard_lifetime_wins_excludes_intra_family(engine) -> None:
    """lifetime_wins or_(bx.base_name != lb_base, bo.base_name != lb_base)
    filters out matches where both bots belong to the same family — an
    intra-family win must not inflate lifetime_wins."""
    foo_v1 = await db_insert_bot(engine, "Foo", version=1, versioned_name="Foo")
    foo_v2 = await db_insert_bot(engine, "Foo", version=2, versioned_name="FooV2")
    await db_insert_match(engine, foo_v1, foo_v2, winner_id=foo_v1, result="x_wins")

    lb = await _leaderboard(engine)
    assert lb["Foo"].lifetime_wins == 0


async def test_leaderboard_lifetime_losses_counts_all_versions(engine) -> None:
    """lifetime_losses bw_inner.base_name == lb_base in the NOT EXISTS — mutating
    to != inverts the filter and miscounts. FooV1 loses as X, FooV2 loses as O
    → lifetime_losses == 2 for Foo, 0 for External."""
    foo_v1 = await db_insert_bot(engine, "Foo", version=1, versioned_name="Foo")
    foo_v2 = await db_insert_bot(engine, "Foo", version=2, versioned_name="FooV2")
    ext = await db_insert_bot(engine, "External")
    await db_insert_match(engine, foo_v1, ext, winner_id=ext, result="o_wins")
    await db_insert_match(engine, ext, foo_v2, winner_id=ext, result="x_wins")

    lb = await _leaderboard(engine)
    assert lb["Foo"].lifetime_losses == 2
    assert lb["External"].lifetime_losses == 0


async def test_leaderboard_lifetime_losses_excludes_intra_family(engine) -> None:
    """lifetime_losses or_(bx.base_name != lb_base, bo.base_name != lb_base)
    filters intra-family matches — a family cannot lose to itself."""
    foo_v1 = await db_insert_bot(engine, "Foo", version=1, versioned_name="Foo")
    foo_v2 = await db_insert_bot(engine, "Foo", version=2, versioned_name="FooV2")
    await db_insert_match(engine, foo_v1, foo_v2, winner_id=foo_v1, result="x_wins")

    lb = await _leaderboard(engine)
    assert lb["Foo"].lifetime_losses == 0


async def test_leaderboard_lifetime_losses_counted_as_x_and_as_o(engine) -> None:
    """lifetime_losses or_(bx.base_name == lb_base, bo.base_name == lb_base) —
    dropping either branch misses losses where the family plays the other role.
    FooBot loses once as X, once as O → lifetime_losses == 2."""
    foo = await db_insert_bot(engine, "Foo")
    ext_a = await db_insert_bot(engine, "ExtA")
    ext_b = await db_insert_bot(engine, "ExtB")
    await db_insert_match(engine, foo, ext_a, winner_id=ext_a, result="o_wins")
    await db_insert_match(engine, ext_b, foo, winner_id=ext_b, result="x_wins")

    lb = await _leaderboard(engine)
    assert lb["Foo"].lifetime_losses == 2


async def test_leaderboard_self_play_forfeit_win_counts_in_lifetime(engine) -> None:
    """A forfeit win earned via self-play must appear in both forfeit_wins and
    lifetime_wins. Reproduces: bot with forfeit_wins=1 but lifetime_wins=0,
    causing the lifetime record to display 0-N instead of 1-N."""
    bot = await db_insert_bot(engine, "CrashBot")
    other = await db_insert_bot(engine, "Other")
    # Self-play: CrashBot X double-moves, CrashBot O wins → forfeit win
    await db_insert_match(engine, bot, bot, winner_id=bot, result="x_forfeit")
    # Normal loss against another bot
    await db_insert_match(engine, bot, other, winner_id=other, result="x_forfeit")

    lb = await _leaderboard(engine)
    assert lb["CrashBot"].forfeit_wins == 1
    assert lb["CrashBot"].lifetime_wins == 1
    assert lb["CrashBot"].losses == 1


# ---------------------------------------------------------------------------
# Matches list
# ---------------------------------------------------------------------------


def test_matches_returns_200(client):
    resp = client.get("/matches")
    assert resp.status_code == 200


def test_matches_empty_state(client):
    resp = client.get("/matches")
    assert "No matches played yet" in resp.text


async def test_matches_shows_both_bot_names(client, engine):
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches")
    assert "AlphaBot" in resp.text
    assert "BetaBot" in resp.text


@pytest.mark.parametrize(
    "result,expected",
    [
        ("x_wins", "AlphaBot won"),
        ("o_wins", "BetaBot won"),
        ("cat", "Cat game"),
        ("x_forfeit", "AlphaBot forfeited"),
        ("o_forfeit", "BetaBot forfeited"),
    ],
)
async def test_matches_result_label(client, engine, result, expected):
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    winner_id = a if result == "x_wins" else b if result == "o_wins" else None
    if result in ("x_forfeit",):
        winner_id = b
    if result in ("o_forfeit",):
        winner_id = a
    await db_insert_match(engine, a, b, winner_id=winner_id, result=result)

    resp = client.get("/matches")
    assert expected in resp.text


async def test_matches_most_recent_first(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    await db_insert_match(
        engine, a, b, winner_id=a, result="x_wins", played_at="2024-01-01 00:00:00"
    )
    await db_insert_match(
        engine, b, a, winner_id=b, result="x_wins", played_at="2024-06-01 00:00:00"
    )

    resp = client.get("/matches")
    # The newer match has BotB as X; the older has BotA as X.
    # BotB-as-X row should come first in the table.
    text = resp.text
    first_occurrence_a = text.index("2024-01-01")
    first_occurrence_b = text.index("2024-06-01")
    assert first_occurrence_b < first_occurrence_a


async def test_matches_contains_link_to_detail(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get("/matches")
    assert f"/matches/{match_id}" in resp.text


async def test_matches_lists_all_matches(client, engine):
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    c = await db_insert_bot(engine, "GammaBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_match(engine, b, c, winner_id=b, result="x_wins")

    resp = client.get("/matches")
    assert "AlphaBot" in resp.text
    assert "BetaBot" in resp.text
    assert "GammaBot" in resp.text


async def test_matches_bot_names_link_to_bot_detail(client, engine):
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches")
    assert '<a href="/bots/AlphaBot">AlphaBot</a>' in resp.text
    assert '<a href="/bots/BetaBot">BetaBot</a>' in resp.text


async def test_match_detail_bot_names_link_to_bot_detail(client, engine):
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert '<a href="/bots/AlphaBot">AlphaBot</a>' in resp.text
    assert '<a href="/bots/BetaBot">BetaBot</a>' in resp.text


async def test_bot_detail_opponent_names_link_to_bot_detail(client, engine):
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/bots/AlphaBot")
    assert '<a href="/bots/BetaBot">BetaBot</a>' in resp.text


async def test_leaderboard_bot_name_links_to_bot_detail(client, engine):
    await db_insert_bot(engine, "AlphaBot")

    resp = client.get("/leaderboard")
    assert "/bots/AlphaBot" in resp.text


async def test_leaderboard_shows_only_latest_version_per_family(client, engine):
    """When MyBot has V1 and V2, only V2 appears as a leaderboard row."""
    await db_insert_bot(engine, "MyBot", submitted_at="2024-01-01 10:00:00")
    await db_insert_bot(
        engine,
        "MyBot",
        versioned_name="MyBotV2",
        version=2,
        submitted_at="2024-01-02 10:00:00",
    )

    resp = client.get("/leaderboard")
    # MyBotV2 appears as the bot name; MyBot (V1) is not its own row.
    assert ">MyBotV2<" in resp.text
    assert '<a href="/bots/MyBot">MyBotV2</a>' in resp.text


async def test_leaderboard_shows_lifetime_column(client, engine):
    a = await db_insert_bot(engine, "AlphaBot")
    b = await db_insert_bot(engine, "BetaBot")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    assert "Lifetime" in resp.text
    # AlphaBot's lifetime row should show 1-0; BetaBot's should show 0-1.
    assert "1-0" in resp.text
    assert "0-1" in resp.text


# ---------------------------------------------------------------------------
# Match detail
# ---------------------------------------------------------------------------


def test_match_detail_404_for_unknown_id(client):
    resp = client.get("/matches/99999")
    assert resp.status_code == 404


async def test_match_detail_returns_200(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert resp.status_code == 200


async def test_match_detail_shows_both_bot_names(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "BotA" in resp.text
    assert "BotB" in resp.text


async def test_match_detail_shows_python_versions(client, engine):
    a = await db_insert_bot(engine, "BotA", python_version="3.11")
    b = await db_insert_bot(engine, "BotB", python_version="3.12")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "Python 3.11" in resp.text
    assert "Python 3.12" in resp.text


async def test_matches_list_shows_python_versions(client, engine):
    a = await db_insert_bot(engine, "BotA", python_version="3.11")
    b = await db_insert_bot(engine, "BotB", python_version="3.12")
    await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches")
    assert "py3.11" in resp.text
    assert "py3.12" in resp.text


async def test_match_detail_shows_result(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "BotA won" in resp.text


async def test_match_detail_shows_moves_in_order(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    await db_insert_move(engine, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    text = resp.text
    assert text.index("Move 1") < text.index("Move 2")


async def test_match_detail_shows_which_bot_made_each_move(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    await db_insert_move(engine, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    text = resp.text
    move1_section = text[text.index("Move 1") : text.index("Move 2")]
    assert "BotA" in move1_section
    move2_section = text[text.index("Move 2") :]
    assert "BotB" in move2_section


async def test_match_detail_shows_error_for_forfeit_move(client, engine):
    a = await db_insert_bot(engine, "GoodBot")
    b = await db_insert_bot(engine, "CrashBot")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="o_forfeit")
    await db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    await db_insert_move(
        engine, match_id, 2, b, BOARD_AFTER_X, error="invalid output: empty response"
    )

    resp = client.get(f"/matches/{match_id}")
    assert "invalid output: empty response" in resp.text


async def test_match_detail_no_moves_shows_empty_state(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get(f"/matches/{match_id}")
    assert "No moves recorded" in resp.text


async def test_match_detail_back_link_to_matches(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get(f"/matches/{match_id}")
    # Anchor to &larr; which only appears in the back-link, not in the nav bar
    assert 'href="/matches">&larr;' in resp.text
    assert "Back to matches" in resp.text


# ---------------------------------------------------------------------------
# Match detail nested under a bot (/bots/{base_name}/matches/{match_id})
# ---------------------------------------------------------------------------


async def test_bot_match_detail_returns_200_when_bot_is_x(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/bots/BotA/matches/{match_id}")
    assert resp.status_code == 200


async def test_bot_match_detail_returns_200_when_bot_is_o(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=b, result="o_wins")

    resp = client.get(f"/bots/BotB/matches/{match_id}")
    assert resp.status_code == 200


async def test_bot_match_detail_404_for_match_not_involving_bot(client, engine):
    await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    c = await db_insert_bot(engine, "BotC")
    match_id = await db_insert_match(engine, b, c, winner_id=b, result="x_wins")

    resp = client.get(f"/bots/BotA/matches/{match_id}")
    assert resp.status_code == 404


def test_bot_match_detail_404_for_unknown_match(client):
    resp = client.get("/bots/BotA/matches/99999")
    assert resp.status_code == 404


async def test_bot_match_detail_back_link_to_bot(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get(f"/bots/BotA/matches/{match_id}")
    assert 'href="/bots/BotA"' in resp.text
    assert "Back to BotA" in resp.text


async def test_bot_detail_links_to_nested_match_url(client, engine):
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/bots/BotA")
    assert f"/bots/BotA/matches/{match_id}" in resp.text


# ---------------------------------------------------------------------------
# Bot family detail (/bots/{base_name})
# ---------------------------------------------------------------------------


def test_bot_family_404_for_unknown_base_name(client):
    resp = client.get("/bots/NoSuchBot")
    assert resp.status_code == 404


async def test_bot_family_lists_all_versions_latest_first(client, engine):
    await db_insert_bot(engine, "MyBot", submitted_at="2024-01-01 10:00:00")
    await db_insert_bot(
        engine,
        "MyBot",
        submitted_at="2024-01-02 10:00:00",
        version=2,
        versioned_name="MyBotV2",
    )
    await db_insert_bot(
        engine,
        "MyBot",
        submitted_at="2024-01-03 10:00:00",
        version=3,
        versioned_name="MyBotV3",
    )

    resp = client.get("/bots/MyBot")
    assert resp.status_code == 200
    body = resp.text
    # Each version has its own <h3> section header.
    assert "<h3>MyBotV3</h3>" in body
    assert "<h3>MyBotV2</h3>" in body
    assert "<h3>MyBot</h3>" in body
    v3 = body.index("<h3>MyBotV3</h3>")
    v2 = body.index("<h3>MyBotV2</h3>")
    v1 = body.index("<h3>MyBot</h3>")
    assert v3 < v2 < v1


async def test_bot_family_groups_matches_under_each_version(client, engine):
    v1 = await db_insert_bot(engine, "MyBot", submitted_at="2024-01-01 10:00:00")
    v2 = await db_insert_bot(
        engine,
        "MyBot",
        submitted_at="2024-01-02 10:00:00",
        version=2,
        versioned_name="MyBotV2",
    )
    other = await db_insert_bot(engine, "OtherBot")

    # V1 plays Other (V1 wins); V2 plays Other (Other wins).
    await db_insert_match(
        engine,
        v1,
        other,
        winner_id=v1,
        result="x_wins",
        played_at="2024-01-05 10:00:00",
    )
    await db_insert_match(
        engine,
        v2,
        other,
        winner_id=other,
        result="o_wins",
        played_at="2024-01-06 10:00:00",
    )

    resp = client.get("/bots/MyBot")
    body = resp.text

    # Both result labels appear and the V2 section precedes the V1 section.
    assert "OtherBot won" in body  # V2's loss
    assert "MyBot won" in body  # V1's win — result label uses the X-side bot name
    assert body.index("<h3>MyBotV2</h3>") < body.index("<h3>MyBot</h3>")


async def test_bot_family_shows_empty_state_for_version_with_no_matches(client, engine):
    await db_insert_bot(engine, "Lonely", submitted_at="2024-01-01 10:00:00")
    resp = client.get("/bots/Lonely")
    assert "No matches yet" in resp.text


async def test_bot_family_intra_family_match_appears_under_both_versions(
    client, engine
):
    """A match between V1 and V2 of the same family is shown under each
    version's section so the row appears twice on the page."""
    v1 = await db_insert_bot(engine, "MyBot", submitted_at="2024-01-01 10:00:00")
    v2 = await db_insert_bot(
        engine,
        "MyBot",
        submitted_at="2024-01-02 10:00:00",
        version=2,
        versioned_name="MyBotV2",
    )
    await db_insert_match(engine, v1, v2, winner_id=v2, result="o_wins")

    resp = client.get("/bots/MyBot")
    body = resp.text
    # The result label "MyBotV2 won" should appear exactly twice — once in
    # each version's section.
    assert body.count("MyBotV2 won") == 2


async def test_bot_family_self_match_appears_exactly_once(client, engine):
    """A true self-pair (bot_x_id == bot_o_id) must NOT be double-counted
    under the bot's version section. Guards `group_matches_by_version` from
    regressing on the `m.bot_o != m.bot_x` dedup."""
    foo = await db_insert_bot(engine, "Foo")
    match_id = await db_insert_match(engine, foo, foo, winner_id=foo, result="x_wins")

    resp = client.get("/bots/Foo")
    body = resp.text
    # The match-detail link is unique per row, so counting it is a precise
    # proxy for "how many times did this match render?".
    link = f"/bots/Foo/matches/{match_id}"
    assert body.count(link) == 1, (
        f"self-match should render once, but found {body.count(link)} "
        f"occurrences of {link!r}"
    )


# ---------------------------------------------------------------------------
# not_found — context dict and status code must both be correct
# ---------------------------------------------------------------------------


def test_not_found_returns_404_status(client) -> None:
    """not_found() must return 404 — guards against context=None or {} dropped
    from TemplateResponse causing a rendering crash that returns 500."""
    resp = client.get("/bots/DoesNotExist")
    assert resp.status_code == 404


def test_not_found_renders_template_without_error(client) -> None:
    """The 404 template must render successfully — context={} must not be
    passed as None (which would crash Jinja2)."""
    resp = client.get("/matches/99999")
    assert resp.status_code == 404
    assert len(resp.text) > 0


# ---------------------------------------------------------------------------
# render_index_response — "bots" context key must be exactly "bots"
# ---------------------------------------------------------------------------


def test_error_response_renders_index_page_successfully(client) -> None:
    """Submission error must return 200 with the index template rendered.
    If the 'bots' context key is renamed, Jinja2 renders the bots loop as
    empty/undefined — the test catches any crash (which would return 500)."""
    resp = client.post(
        "/submit",
        files={"file": ("bot.py", b"import sys\n", "text/plain")},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# match_detail — back_url context key must be exactly "back_url"
# ---------------------------------------------------------------------------


async def test_match_detail_back_url_is_accessible_in_template(client, engine) -> None:
    """The 'back_url' context key must reach the template by its exact name.
    Renaming it to 'XXback_urlXX' or 'BACK_URL' renders href="" instead of
    href="/matches". base.html always has href="/matches" in the nav, so we
    anchor the check to the &larr; arrow that's unique to the back-link."""
    a = await db_insert_bot(engine, "BotA")
    b = await db_insert_bot(engine, "BotB")
    match_id = await db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    # &larr; appears only in the back-link <a>, not in the nav. If back_url
    # is undefined, the rendered href is "" and this check fails.
    assert 'href="/matches">&larr;' in resp.text
