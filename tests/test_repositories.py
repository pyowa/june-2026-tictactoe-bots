"""Unit tests for repository methods not exercised by web routes / scripts.

Most repository methods get covered via the web layer's end-to-end tests
(the leaderboard/match/family pages all hit a repo). The methods here —
`by_id`, `by_versioned_name`, `by_ids`, `MatchRepository.record` — are part
of the public contract but don't have a route caller today, so they need
their own coverage."""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import db.session
from entities.bot.repository import BotRepository
from entities.match.model import Match
from entities.match.repository import MatchRepository
from entities.move.model import Move as MoveModel
from runner.engine import MatchResult, Move
from tests.conftest import TEST_ASYNC_URL, db_insert_bot, db_insert_match


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


async def test_match_record_o_forfeit_winner_is_bot_x(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "ForfeitX")
    bot_o = await db_insert_bot(engine, "ForfeitO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(result="o_forfeit", moves=[])
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-oforfeit")
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
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-xforfeit")
    async with factory() as session:
        row = (await session.execute(select(Match))).scalar_one()
    assert row.result == "x_forfeit"
    assert row.winner_id == bot_o


async def test_match_record_stores_correlation_id(
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


async def test_match_record_assigns_x_move_to_bot_x(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "MoveX")
    bot_o = await db_insert_bot(engine, "MoveO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(
        result="x_wins",
        moves=[Move(move_number=1, player="x", board="X|.|.\n.|.|.\n.|.|.")],
    )
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-moves")
    async with factory() as session:
        row = (await session.execute(select(MoveModel))).scalar_one()
    assert row.bot_id == bot_x


async def test_match_record_assigns_o_move_to_bot_o(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "OmoveX")
    bot_o = await db_insert_bot(engine, "OmoveO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(
        result="o_wins",
        moves=[Move(move_number=1, player="o", board="X|.|.\n.|O|.\n.|.|.")],
    )
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-omoves")
    async with factory() as session:
        row = (await session.execute(select(MoveModel))).scalar_one()
    assert row.bot_id == bot_o


async def test_match_record_stores_move_error(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "ErrX")
    bot_o = await db_insert_bot(engine, "ErrO")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = MatchResult(
        result="x_forfeit",
        moves=[
            Move(
                move_number=1, player="x",
                board=".|.|.\n.|.|.\n.|.|.", error="timeout",
            )
        ],
    )
    async with factory() as session:
        await MatchRepository(session).record(bot_x, bot_o, result, "cid-err")
    async with factory() as session:
        row = (await session.execute(select(MoveModel))).scalar_one()
    assert row.error == "timeout"


async def test_match_select_winner_field_populated_on_win(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "WinnerX")
    bot_o = await db_insert_bot(engine, "WinnerO")
    await db_insert_match(engine, bot_x, bot_o, winner_id=bot_x, result="x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = await MatchRepository(session).by_id(1)
    assert row is not None
    assert row.winner == "WinnerX"


async def test_match_select_winner_null_for_draw(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot_x = await db_insert_bot(engine, "DrawWX")
    bot_o = await db_insert_bot(engine, "DrawWO")
    await db_insert_match(engine, bot_x, bot_o, winner_id=None, result="cat")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_all()
    assert len(rows) == 1
    assert rows[0].winner is None


async def test_list_for_bot_orders_most_recent_first(
    engine: AsyncEngine, _bound_db: None
) -> None:
    bot = await db_insert_bot(engine, "OrderBot")
    other = await db_insert_bot(engine, "Other")
    await db_insert_match(
        engine, bot, other, winner_id=bot, result="x_wins",
        played_at="2024-01-01 10:00:00",
    )
    await db_insert_match(
        engine, bot, other, winner_id=other, result="o_wins",
        played_at="2024-06-01 10:00:00",
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_for_bot("OrderBot")
    assert len(rows) == 2
    assert rows[0].played_at > rows[1].played_at


async def test_list_for_bot_returns_match_where_bot_is_o_only(
    engine: AsyncEngine, _bound_db: None
) -> None:
    a = await db_insert_bot(engine, "AsO")
    b = await db_insert_bot(engine, "OpponentBot")
    await db_insert_match(engine, b, a, winner_id=b, result="x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await MatchRepository(session).list_for_bot("AsO")
    assert len(rows) == 1
    assert rows[0].bot_o == "AsO"


async def test_list_for_homepage_orders_by_submission_descending(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "EarlyBot", submitted_at="2024-01-01 00:00:00")
    await db_insert_bot(engine, "LateBot", submitted_at="2024-06-01 00:00:00")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).list_for_homepage()
    assert rows[0].versioned_name == "LateBot"
    assert rows[1].versioned_name == "EarlyBot"


async def test_bot_family_stats_correct_for_all_result_types(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """family() must return correct clean_wins, forfeit_wins, draws, losses."""
    bot = await db_insert_bot(engine, "FamBot")
    other = await db_insert_bot(engine, "Other")
    await db_insert_match(engine, bot, other, winner_id=bot, result="x_wins")
    await db_insert_match(engine, other, bot, winner_id=bot, result="o_wins")
    await db_insert_match(engine, bot, other, winner_id=bot, result="o_forfeit")
    await db_insert_match(engine, other, bot, winner_id=bot, result="x_forfeit")
    await db_insert_match(engine, bot, other, winner_id=None, result="cat")
    await db_insert_match(engine, other, bot, winner_id=other, result="x_wins")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("FamBot")
    assert len(rows) == 1
    r = rows[0]
    assert r.clean_wins == 2
    assert r.forfeit_wins == 2
    assert r.draws == 1
    assert r.losses == 1


async def test_bot_family_stats_bot_as_o_draws_counted(
    engine: AsyncEngine, _bound_db: None
) -> None:
    """draws must count the bot whether it plays as X or as O."""
    bot = await db_insert_bot(engine, "DrawFam")
    other = await db_insert_bot(engine, "DrawOther")
    await db_insert_match(engine, other, bot, winner_id=None, result="cat")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("DrawFam")
    assert rows[0].draws == 1


async def test_bot_family_ordering_latest_version_first(
    engine: AsyncEngine, _bound_db: None
) -> None:
    await db_insert_bot(engine, "VerBot", version=1, versioned_name="VerBot")
    await db_insert_bot(engine, "VerBot", version=2, versioned_name="VerBotV2")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await BotRepository(session).family("VerBot")
    assert rows[0].version == 2
    assert rows[1].version == 1
