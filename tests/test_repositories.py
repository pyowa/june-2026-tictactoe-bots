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
