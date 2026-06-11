"""Unit tests for repository methods not exercised by web routes / scripts.

Most repository methods get covered via the web layer's end-to-end tests
(the leaderboard/match/family pages all hit a repo). The methods here —
`by_id`, `by_versioned_name` — are part of the public contract but don't
have a route caller today, so they need their own coverage."""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import db.session
from entities.bot.repository import BotRepository
from tests.conftest import TEST_ASYNC_URL, db_insert_bot


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
