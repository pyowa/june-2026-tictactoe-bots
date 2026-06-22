"""Direct BotRepository.leaderboard() tests.

Each test targets one specific mutation the acceptance suite couldn't catch:
a flipped comparison, a dropped literal, or a missing filter clause.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import db.session
from entities.bot.repository import BotRepository
from tests.conftest import TEST_ASYNC_URL, db_insert_bot, db_insert_match


@pytest_asyncio.fixture()
async def _bound_db(engine: AsyncEngine) -> AsyncIterator[None]:
    db.session.reconfigure(TEST_ASYNC_URL)
    yield


async def _leaderboard(engine: AsyncEngine) -> dict[str, Any]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).leaderboard()
    return {r.versioned_name: r for r in rows}


# ---------------------------------------------------------------------------
# mutmut_46 — only latest version per family appears
# ---------------------------------------------------------------------------


async def test_leaderboard_shows_only_latest_version(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "Alpha", versioned_name="Alpha", version=1)
    await db_insert_bot(engine, "Alpha", versioned_name="AlphaV2", version=2)

    rows = await _leaderboard(engine)

    assert "AlphaV2" in rows
    assert "Alpha" not in rows


# ---------------------------------------------------------------------------
# clean_wins — both x_wins and o_wins count; opponent gets zero
# ---------------------------------------------------------------------------


async def test_clean_wins_counts_x_wins_and_o_wins(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "Hero")
    foe = await db_insert_bot(engine, "Foe")

    await db_insert_match(engine, hero, foe, hero, "x_wins")
    await db_insert_match(engine, foe, hero, hero, "o_wins")

    rows = await _leaderboard(engine)

    assert rows["Hero"].clean_wins == 2
    assert rows["Foe"].clean_wins == 0


async def test_clean_wins_winner_id_must_match(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "Champ")
    foe = await db_insert_bot(engine, "Loser")

    await db_insert_match(engine, hero, foe, foe, "x_wins")

    rows = await _leaderboard(engine)

    assert rows["Champ"].clean_wins == 0
    assert rows["Loser"].clean_wins == 1


# ---------------------------------------------------------------------------
# forfeit_wins — x_forfeit and o_forfeit both count
# ---------------------------------------------------------------------------


async def test_forfeit_wins_counts_x_forfeit(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "ForfeitHero")
    foe = await db_insert_bot(engine, "ForfeitFoe")

    await db_insert_match(engine, hero, foe, hero, "x_forfeit")

    rows = await _leaderboard(engine)

    assert rows["ForfeitHero"].forfeit_wins == 1
    assert rows["ForfeitFoe"].forfeit_wins == 0


async def test_forfeit_wins_counts_o_forfeit(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "OForfeitHero")
    foe = await db_insert_bot(engine, "OForfeitFoe")

    await db_insert_match(engine, foe, hero, hero, "o_forfeit")

    rows = await _leaderboard(engine)

    assert rows["OForfeitHero"].forfeit_wins == 1
    assert rows["OForfeitFoe"].forfeit_wins == 0


# ---------------------------------------------------------------------------
# draws — credited as X and as O; non-draw does not count
# ---------------------------------------------------------------------------


async def test_draws_credits_x_side_and_o_side(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "DrawHero")
    foe = await db_insert_bot(engine, "DrawFoe")

    await db_insert_match(engine, hero, foe, None, "cat")
    await db_insert_match(engine, foe, hero, None, "cat")

    rows = await _leaderboard(engine)

    assert rows["DrawHero"].draws == 2
    assert rows["DrawFoe"].draws == 2


async def test_draws_does_not_count_non_draw(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "WinHero")
    foe = await db_insert_bot(engine, "WinFoe")

    await db_insert_match(engine, hero, foe, hero, "x_wins")

    rows = await _leaderboard(engine)

    assert rows["WinHero"].draws == 0
    assert rows["WinFoe"].draws == 0


# ---------------------------------------------------------------------------
# losses — credited as X and as O; draw does not count as loss
# ---------------------------------------------------------------------------


async def test_losses_credits_x_side_and_o_side(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "LoseHero")
    foe = await db_insert_bot(engine, "LoseFoe")

    await db_insert_match(engine, hero, foe, foe, "o_wins")
    await db_insert_match(engine, foe, hero, foe, "x_wins")

    rows = await _leaderboard(engine)

    assert rows["LoseHero"].losses == 2
    assert rows["LoseFoe"].losses == 0


async def test_draw_does_not_count_as_loss(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "DrawNoLoss")
    foe = await db_insert_bot(engine, "DrawNoLossFoe")

    await db_insert_match(engine, hero, foe, None, "cat")

    rows = await _leaderboard(engine)

    assert rows["DrawNoLoss"].losses == 0
    assert rows["DrawNoLossFoe"].losses == 0


async def test_losses_counts_forfeit_loss(engine: AsyncEngine, _bound_db: None) -> None:
    hero = await db_insert_bot(engine, "ForfeitLoser")
    foe = await db_insert_bot(engine, "ForfeitWinner")

    await db_insert_match(engine, hero, foe, foe, "x_forfeit")

    rows = await _leaderboard(engine)

    assert rows["ForfeitLoser"].losses == 1
    assert rows["ForfeitWinner"].losses == 0


# ---------------------------------------------------------------------------
# lifetime_wins — rolls up across versions; intra-family excluded
# ---------------------------------------------------------------------------


async def test_lifetime_wins_rolls_up_across_versions(
    engine: AsyncEngine, _bound_db: None
) -> None:
    v1 = await db_insert_bot(engine, "Fam", versioned_name="Fam", version=1)
    v2 = await db_insert_bot(engine, "Fam", versioned_name="FamV2", version=2)
    foe = await db_insert_bot(engine, "FamFoe")

    await db_insert_match(engine, v1, foe, v1, "x_wins")
    await db_insert_match(engine, foe, v2, v2, "o_wins")

    rows = await _leaderboard(engine)

    assert rows["FamV2"].lifetime_wins == 2


async def test_lifetime_wins_excludes_intra_family(
    engine: AsyncEngine, _bound_db: None
) -> None:
    v1 = await db_insert_bot(engine, "Self", versioned_name="Self", version=1)
    v2 = await db_insert_bot(engine, "Self", versioned_name="SelfV2", version=2)

    await db_insert_match(engine, v1, v2, v1, "x_wins")

    rows = await _leaderboard(engine)

    assert rows["SelfV2"].lifetime_wins == 0


# ---------------------------------------------------------------------------
# lifetime_losses — rolls up; intra-family excluded; family-as-X and as-O
# ---------------------------------------------------------------------------


async def test_lifetime_losses_rolls_up_across_versions(
    engine: AsyncEngine, _bound_db: None
) -> None:
    v1 = await db_insert_bot(engine, "LoseFam", versioned_name="LoseFam", version=1)
    v2 = await db_insert_bot(engine, "LoseFam", versioned_name="LoseFamV2", version=2)
    foe = await db_insert_bot(engine, "LoseFamFoe")

    await db_insert_match(engine, v1, foe, foe, "o_wins")
    await db_insert_match(engine, foe, v2, foe, "x_wins")

    rows = await _leaderboard(engine)

    assert rows["LoseFamV2"].lifetime_losses == 2


async def test_lifetime_losses_excludes_intra_family(
    engine: AsyncEngine, _bound_db: None
) -> None:
    v1 = await db_insert_bot(engine, "SelfLose", versioned_name="SelfLose", version=1)
    v2 = await db_insert_bot(engine, "SelfLose", versioned_name="SelfLoseV2", version=2)

    await db_insert_match(engine, v1, v2, v2, "o_wins")

    rows = await _leaderboard(engine)

    assert rows["SelfLoseV2"].lifetime_losses == 0


async def test_lifetime_losses_counts_x_side_and_o_side(
    engine: AsyncEngine, _bound_db: None
) -> None:
    v1 = await db_insert_bot(engine, "SideLose", versioned_name="SideLose", version=1)
    v2 = await db_insert_bot(engine, "SideLose", versioned_name="SideLoseV2", version=2)
    foe = await db_insert_bot(engine, "SideLoseFoe")

    await db_insert_match(engine, v1, foe, foe, "o_wins")
    await db_insert_match(engine, foe, v2, foe, "x_wins")

    rows = await _leaderboard(engine)

    assert rows["SideLoseV2"].lifetime_losses == 2
    assert rows["SideLoseFoe"].lifetime_losses == 0


async def test_lifetime_losses_winner_not_in_family_join(
    engine: AsyncEngine, _bound_db: None
) -> None:
    v1 = await db_insert_bot(
        engine, "WinnerJoin", versioned_name="WinnerJoin", version=1
    )
    v2 = await db_insert_bot(
        engine, "WinnerJoin", versioned_name="WinnerJoinV2", version=2
    )
    foe = await db_insert_bot(engine, "WinnerJoinFoe")

    await db_insert_match(engine, v1, foe, foe, "o_wins")
    await db_insert_match(engine, v1, v2, v1, "x_wins")

    rows = await _leaderboard(engine)

    assert rows["WinnerJoinV2"].lifetime_losses == 1


# ---------------------------------------------------------------------------
# mutmut_284 / Tier B — ORDER BY submitted_at breaks ties
# ---------------------------------------------------------------------------


async def test_leaderboard_tie_broken_by_submitted_at(
    engine: AsyncEngine, _bound_db: None
) -> None:
    early = await db_insert_bot(engine, "Early", submitted_at="2026-01-01T00:00:00")
    late = await db_insert_bot(engine, "Late", submitted_at="2026-06-01T00:00:00")
    foe = await db_insert_bot(engine, "Fodder", submitted_at="2026-01-01T00:00:00")

    await db_insert_match(engine, early, foe, early, "x_wins")
    await db_insert_match(engine, late, foe, late, "x_wins")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).leaderboard()

    names = [r.versioned_name for r in rows]
    assert names.index("Early") < names.index("Late")


# ---------------------------------------------------------------------------
# lifetime_losses — null winner_id counts as a loss
# ---------------------------------------------------------------------------


async def test_lifetime_losses_counts_null_winner(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "NullWinLose")
    foe = await db_insert_bot(engine, "NullWinFoe")

    await db_insert_match(engine, hero, foe, None, "x_forfeit")

    rows = await _leaderboard(engine)

    assert rows["NullWinLose"].lifetime_losses == 1
