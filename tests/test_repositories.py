"""Unit tests for repository methods not exercised by web routes / scripts.

Most repository methods get covered via the web layer's end-to-end tests
(the leaderboard/match/family pages all hit a repo). The methods here —
`by_id`, `by_versioned_name`, `by_ids`, `MatchRepository.record` — are part
of the public contract but don't have a route caller today, so they need
their own coverage."""

import inspect
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import db.session
from entities.bot.repository import BotRepository
from entities.match.model import Match
from entities.match.repository import MatchRepository
from entities.move.model import Move as MoveModel
from entities.move.repository import MoveRepository
from runner.engine import MatchResult, Move
from tests.conftest import (
    TEST_ASYNC_URL,
    db_insert_bot,
    db_insert_match,
    db_insert_move,
)


@pytest_asyncio.fixture()
async def _bound_db(engine: AsyncEngine) -> AsyncIterator[None]:
    db.session.reconfigure(TEST_ASYNC_URL)
    yield


async def test_bot_by_id_returns_match(engine: AsyncEngine, _bound_db: None) -> None:
    bot_id = await db_insert_bot(engine, "FindMe")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        found = await BotRepository(session).by_id(bot_id)
    assert found is not None
    assert found.base_name == "FindMe"


async def test_bot_by_id_returns_none_for_missing(
    engine: AsyncEngine, _bound_db: None
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        result = await BotRepository(session).by_id(999_999)
    assert result is None


async def test_bot_by_versioned_name_returns_match(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "Family", version=2, versioned_name="FamilyV2")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        found = await BotRepository(session).by_versioned_name("FamilyV2")
    assert found is not None
    assert found.version == 2


async def test_bot_by_versioned_name_returns_none_for_missing(
    engine: AsyncEngine, _bound_db: None
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        result = await BotRepository(session).by_versioned_name("NoSuchBot")
    assert result is None


async def test_ready_bots_returns_only_pod_ready(
    engine: AsyncEngine, _bound_db: None
) -> None:
    ready_id = await db_insert_bot(engine, "ReadyBot")
    await db_insert_bot(engine, "NotReadyBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repo = BotRepository(session)
        await repo.set_pod_ready(ready_id, "pod-ready-bot-abc")
    async with factory() as session:
        bots = await BotRepository(session).ready_bots()
    assert len(bots) == 1
    assert bots[0].base_name == "ReadyBot"
    assert bots[0].pod_name == "pod-ready-bot-abc"
    assert bots[0].pod_ready is True


async def test_ready_bots_returns_empty_when_none_ready(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "SomeBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        bots = await BotRepository(session).ready_bots()
    assert bots == []


async def test_set_pod_ready_sets_pod_name_and_ready(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_id = await db_insert_bot(engine, "PodBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        bot = await BotRepository(session).by_id(bot_id)
        assert bot is not None
        assert bot.pod_ready is False
        assert bot.pod_name is None
    async with factory() as session:
        await BotRepository(session).set_pod_ready(bot_id, "my-pod-xyz")
    async with factory() as session:
        bot = await BotRepository(session).by_id(bot_id)
        assert bot is not None
        assert bot.pod_ready is True
        assert bot.pod_name == "my-pod-xyz"


async def test_new_bot_defaults_pod_ready_false(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_id = await db_insert_bot(engine, "DefaultBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        bot = await BotRepository(session).by_id(bot_id)
    assert bot is not None
    assert bot.pod_ready is False
    assert bot.pod_name is None


async def test_by_ids_returns_bots_keyed_by_id(
    engine: AsyncEngine, _bound_db: None
) -> None:
    id_a = await db_insert_bot(engine, "BotA")
    id_b = await db_insert_bot(engine, "BotB")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        result = await BotRepository(session).by_ids([id_a, id_b])
    assert set(result.keys()) == {id_a, id_b}
    assert result[id_a].base_name == "BotA"
    assert result[id_b].base_name == "BotB"


async def test_match_record_persists_win(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "BotX")
    bot_o = await db_insert_bot(engine, "BotO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(
        result="x_wins",
        moves=[Move(1, "x", "X|.|.\n.|.|.\n.|.|.")],
    )
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-123")
    async with factory() as session:
        row = (await session.execute(select(Match))).scalar_one()
    assert row.result == "x_wins"
    assert row.winner_id == bot_x
    assert row.bot_x_id == bot_x
    assert row.bot_o_id == bot_o


async def test_match_record_persists_o_wins(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "OWinX")
    bot_o = await db_insert_bot(engine, "OWinO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(result="o_wins", moves=[])
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-o")
    async with factory() as session:
        row = (await session.execute(select(Match))).scalar_one()
    assert row.result == "o_wins"
    assert row.winner_id == bot_o


async def test_match_record_persists_draw(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "DrawX")
    bot_o = await db_insert_bot(engine, "DrawO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(result="cat", moves=[])
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-draw")
    async with factory() as session:
        row = (await session.execute(select(Match))).scalar_one()
    assert row.result == "cat"
    assert row.winner_id is None


# ---------------------------------------------------------------------------
# BotRepository.create — default python_version
# ---------------------------------------------------------------------------


async def test_bot_create_default_python_version(
    engine: AsyncEngine, _bound_db: None
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        bot = await BotRepository(session).create(
            base_name="DefaultPy",
            versioned_name="DefaultPy",
            version=1,
            owner_token="tok",
        )
        await session.commit()
    async with factory() as session:
        found = await BotRepository(session).by_id(bot.id)
    assert found is not None
    assert found.python_version == "3"


# ---------------------------------------------------------------------------
# MatchRepository.list_all — ordering
# ---------------------------------------------------------------------------


async def test_list_all_winner_name_matches_winning_bot(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "WinX")
    bot_o = await db_insert_bot(engine, "WinO")
    await db_insert_match(engine, bot_x, bot_o, bot_x, "x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_all()
    assert rows[0].winner == "WinX"


async def test_list_all_orders_by_played_at_desc(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_a = await db_insert_bot(engine, "OrdA")
    bot_b = await db_insert_bot(engine, "OrdB")
    await db_insert_match(engine, bot_a, bot_b, None, "cat", "2024-01-01T10:00:00")
    await db_insert_match(engine, bot_a, bot_b, None, "cat", "2024-01-02T10:00:00")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_all()
    assert rows[0].played_at > rows[1].played_at


# ---------------------------------------------------------------------------
# MatchRepository.list_for_bot — X/O sides and ordering
# ---------------------------------------------------------------------------


async def test_list_for_bot_includes_match_as_x(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "Hero")
    foe = await db_insert_bot(engine, "Foe")
    await db_insert_match(engine, hero, foe, None, "cat")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_for_bot("Hero")
    assert len(rows) == 1
    assert rows[0].bot_x == "Hero"


async def test_list_for_bot_includes_match_as_o(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "HeroO")
    foe = await db_insert_bot(engine, "FoeO")
    await db_insert_match(engine, foe, hero, None, "cat")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_for_bot("HeroO")
    assert len(rows) == 1
    assert rows[0].bot_o == "HeroO"


async def test_list_for_bot_orders_by_played_at_desc(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_a = await db_insert_bot(engine, "ForBotA")
    bot_b = await db_insert_bot(engine, "ForBotB")
    await db_insert_match(engine, bot_a, bot_b, None, "cat", "2024-03-01T00:00:00")
    await db_insert_match(engine, bot_a, bot_b, None, "cat", "2024-03-02T00:00:00")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_for_bot("ForBotA")
    assert rows[0].played_at > rows[1].played_at


# ---------------------------------------------------------------------------
# BotRepository.family — ordering
# ---------------------------------------------------------------------------


async def test_family_returns_versions_newest_first(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "Fam", version=1, versioned_name="Fam")
    await db_insert_bot(engine, "Fam", version=2, versioned_name="FamV2")
    await db_insert_bot(engine, "Fam", version=3, versioned_name="FamV3")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("Fam")
    versions = [r.version for r in rows]
    assert versions == sorted(versions, reverse=True)


# ---------------------------------------------------------------------------
# MatchRepository.record — forfeit winner assignment
# ---------------------------------------------------------------------------


async def test_match_record_o_forfeit_winner_is_bot_x(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "OForfX")
    bot_o = await db_insert_bot(engine, "OForfO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(result="o_forfeit", moves=[])
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-oforf")
    async with factory() as session:
        row = (await session.execute(select(Match))).scalar_one()
    assert row.result == "o_forfeit"
    assert row.winner_id == bot_x


async def test_match_record_x_forfeit_winner_is_bot_o(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "XForfX")
    bot_o = await db_insert_bot(engine, "XForfO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(result="x_forfeit", moves=[])
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-xforf")
    async with factory() as session:
        row = (await session.execute(select(Match))).scalar_one()
    assert row.result == "x_forfeit"
    assert row.winner_id == bot_o


# ---------------------------------------------------------------------------
# MatchRepository.record — correlation_id persisted
# ---------------------------------------------------------------------------


async def test_match_record_persists_correlation_id(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "CidX")
    bot_o = await db_insert_bot(engine, "CidO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(result="x_wins", moves=[])
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "my-correlation-id")
    async with factory() as session:
        row = (await session.execute(select(Match))).scalar_one()
    assert row.correlation_id == "my-correlation-id"


# ---------------------------------------------------------------------------
# MatchRepository.record — move bot_id assignment (player "x" vs "o")
# ---------------------------------------------------------------------------


async def test_match_record_assigns_moves_to_correct_bots(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "MoveX")
    bot_o = await db_insert_bot(engine, "MoveO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(
        result="x_wins",
        moves=[
            Move(move_number=1, player="x", board="X|.|.\n.|.|.\n.|.|."),
            Move(move_number=2, player="o", board="X|.|.\n.|O|.\n.|.|."),
        ],
    )
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-moves")
    async with factory() as session:
        moves = list(
            (
                await session.execute(
                    select(MoveModel).order_by(MoveModel.move_number)
                )
            )
            .scalars()
            .all()
        )
    assert moves[0].bot_id == bot_x
    assert moves[1].bot_id == bot_o


# ---------------------------------------------------------------------------
# MatchRepository.record — move error field persisted
# ---------------------------------------------------------------------------


async def test_match_record_persists_move_error(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "ErrX")
    bot_o = await db_insert_bot(engine, "ErrO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(
        result="x_forfeit",
        moves=[
            Move(
                move_number=1,
                player="x",
                board=".|.|.\n.|.|.\n.|.|.",
                error="runtime error",
            ),
        ],
    )
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-err")
    async with factory() as session:
        move = (await session.execute(select(MoveModel))).scalar_one()
    assert move.error == "runtime error"


# ---------------------------------------------------------------------------
# BotRepository.create — default python_version signature
# ---------------------------------------------------------------------------


def test_bot_create_default_python_version_is_3() -> None:
    sig = inspect.signature(BotRepository.create)
    assert sig.parameters["python_version"].default == "3"


# ---------------------------------------------------------------------------
# BotRepository.family — clean_wins excludes forfeits
# ---------------------------------------------------------------------------


async def test_family_clean_wins_counts_x_wins_and_o_wins(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamCWBoth")
    foe = await db_insert_bot(engine, "FamCWBothFoe")
    await db_insert_match(engine, hero, foe, hero, "x_wins")
    await db_insert_match(engine, foe, hero, hero, "o_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamCWBoth")
    assert rows[0].clean_wins == 2


async def test_family_clean_wins_winner_must_be_bot(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamCWLoser")
    foe = await db_insert_bot(engine, "FamCWLoserFoe")
    await db_insert_match(engine, hero, foe, foe, "o_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamCWLoser")
    assert rows[0].clean_wins == 0


async def test_family_forfeit_wins_winner_must_be_bot(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamFWLoser")
    foe = await db_insert_bot(engine, "FamFWLoserFoe")
    await db_insert_match(engine, hero, foe, foe, "x_forfeit")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamFWLoser")
    assert rows[0].forfeit_wins == 0


async def test_family_forfeit_wins_excludes_clean_wins(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamFWClean")
    foe = await db_insert_bot(engine, "FamFWCleanFoe")
    await db_insert_match(engine, hero, foe, hero, "x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamFWClean")
    assert rows[0].forfeit_wins == 0
    assert rows[0].clean_wins == 1


async def test_family_clean_wins_excludes_forfeit_wins(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamCW")
    foe = await db_insert_bot(engine, "FamCWFoe")
    await db_insert_match(engine, hero, foe, hero, "x_forfeit")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamCW")
    row = rows[0]
    assert row.clean_wins == 0
    assert row.forfeit_wins == 1


async def test_family_draws_only_counts_cat_results(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "DrawCat")
    foe = await db_insert_bot(engine, "DrawCatFoe")
    await db_insert_match(engine, hero, foe, hero, "x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("DrawCat")
    assert rows[0].draws == 0


async def test_family_draws_counts_o_side(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "DrawOSide")
    foe = await db_insert_bot(engine, "DrawOSideFoe")
    await db_insert_match(engine, foe, hero, None, "cat")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("DrawOSide")
    assert rows[0].draws == 1


async def test_family_submitted_at_is_populated(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "FamSubAt", submitted_at="2026-01-15T10:00:00")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamSubAt")
    assert rows[0].submitted_at is not None


async def test_family_draws_only_counts_matches_bot_participates_in(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "DrawPart")
    bystander_a = await db_insert_bot(engine, "DrawBystA")
    bystander_b = await db_insert_bot(engine, "DrawBystB")
    await db_insert_match(engine, bystander_a, bystander_b, None, "cat")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("DrawPart")
    assert rows[0].draws == 0


# ---------------------------------------------------------------------------
# BotRepository.family — losses
# ---------------------------------------------------------------------------


async def test_family_losses_counts_x_side_loss(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamLoseX")
    foe = await db_insert_bot(engine, "FamLoseXFoe")
    await db_insert_match(engine, hero, foe, foe, "o_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamLoseX")
    assert rows[0].losses == 1


async def test_family_losses_counts_o_side_loss(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamLoseO")
    foe = await db_insert_bot(engine, "FamLoseOFoe")
    await db_insert_match(engine, foe, hero, foe, "x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamLoseO")
    assert rows[0].losses == 1


async def test_family_losses_does_not_count_wins(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamWinNoLoss")
    foe = await db_insert_bot(engine, "FamWinNoLossFoe")
    await db_insert_match(engine, hero, foe, hero, "x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamWinNoLoss")
    assert rows[0].losses == 0


async def test_family_losses_does_not_count_draws(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamDrawNoLoss")
    foe = await db_insert_bot(engine, "FamDrawNoLossFoe")
    await db_insert_match(engine, hero, foe, None, "cat")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamDrawNoLoss")
    assert rows[0].losses == 0


async def test_family_losses_excludes_non_participant(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "FamLoseNoPart")
    bystander_a = await db_insert_bot(engine, "FamLoseByA")
    bystander_b = await db_insert_bot(engine, "FamLoseByB")
    await db_insert_match(engine, bystander_a, bystander_b, bystander_a, "x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamLoseNoPart")
    assert rows[0].losses == 0


async def test_family_losses_counts_null_winner(
    engine: AsyncEngine, _bound_db: None
) -> None:
    hero = await db_insert_bot(engine, "FamLoseNull")
    foe = await db_insert_bot(engine, "FamLoseNullFoe")
    await db_insert_match(engine, hero, foe, None, "x_forfeit")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamLoseNull")
    assert rows[0].losses == 1


# ---------------------------------------------------------------------------
# MoveRepository.for_match — ordered by move_number
# ---------------------------------------------------------------------------


async def test_for_match_orders_by_move_number(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "MoveOrdX")
    bot_o = await db_insert_bot(engine, "MoveOrdO")
    match_id = await db_insert_match(engine, bot_x, bot_o, bot_x, "x_wins")
    await db_insert_move(engine, match_id, 3, bot_x, "X|X|X\n.|.|.\n.|.|.")
    await db_insert_move(engine, match_id, 1, bot_x, "X|.|.\n.|.|.\n.|.|.")
    await db_insert_move(engine, match_id, 2, bot_o, "X|.|.\n.|O|.\n.|.|.")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MoveRepository(session).for_match(match_id)
    assert [r.move_number for r in rows] == [1, 2, 3]
