import os
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

import db.session
import web.main
from db.base import Base
from entities.bot.model import Bot
from entities.match.model import Match
from entities.move.model import Move
from messaging.contracts import BuildPodMessage
from messaging.queue import MatchJob
from web.dependencies import get_queue

BOT_TEMPLATE = '"""\nname: {name}\n"""\nimport sys\n'


def make_bot(name: str, extra: str = "") -> bytes:
    return (BOT_TEMPLATE.format(name=name) + extra).encode()


def upload(client: TestClient, name: str, extra: str = "") -> Any:
    return client.post(
        "/submit",
        files={"file": ("bot.py", make_bot(name, extra), "text/plain")},
    )


# ---------------------------------------------------------------------------
# Test database.
#
# Tests share the running `docker compose` Postgres but live in their own
# database (`ttt_test`) so they never collide with dev data. The conftest
# creates the database on first use and drops/recreates the schema once per
# session; per-test isolation is via TRUNCATE.
# ---------------------------------------------------------------------------

PG_HOST = os.environ.get("PGHOST", "localhost")
PG_PORT = int(os.environ.get("PGPORT", "5432"))
PG_USER = os.environ.get("PGUSER", "ttt")
PG_PASSWORD = os.environ.get("PGPASSWORD", "ttt")
PG_ADMIN_DB = os.environ.get("PGDATABASE", "ttt")
TEST_DB_NAME = "ttt_test"

_ASYNC_BASE_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}"
_SYNC_BASE_URL = f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}"
TEST_ASYNC_URL = f"{_ASYNC_BASE_URL}/{TEST_DB_NAME}"
# Only used by the sync admin-bootstrap below (CREATE DATABASE before any
# async machinery is alive). Production / runtime never sees this URL.
_TEST_ADMIN_SYNC_URL = f"{_SYNC_BASE_URL}/{PG_ADMIN_DB}"


def _ensure_test_database_exists() -> None:
    """Connect to the admin DB and create `ttt_test` if it isn't there.

    This stays sync (psycopg2) because it runs once, at session start, before
    the async engine is bound — bootstrapping the test database itself can't
    use the async session factory that *targets* that database."""
    admin_engine = create_engine(_TEST_ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        # Raw SQL: pg_database is a Postgres system catalog, not an ORM model.
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": TEST_DB_NAME},
        ).first()
        if not exists:
            # Raw SQL: CREATE DATABASE is dialect-specific DDL with no
            # SQLAlchemy construct, and its name can't be parameter-bound.
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema() -> None:
    """Create the test database (if needed) and apply the schema once."""
    _ensure_test_database_exists()
    engine = create_async_engine(TEST_ASYNC_URL)
    # `Base.metadata.drop_all` / `.create_all` are sync APIs; the async
    # equivalent is to call them via `conn.run_sync` on an async connection.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Per-test fixtures.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def engine() -> AsyncIterator[AsyncEngine]:
    """An async engine bound to the test database. Truncates data tables
    before each test so tests don't see each other's rows."""
    eng = create_async_engine(TEST_ASYNC_URL)
    async with eng.begin() as conn:
        # Raw SQL: TRUNCATE is DDL with no ORM equivalent. The closest ORM
        # form (three deletes) is slower, loses identity reset, and runs as
        # multiple statements instead of one atomic op.
        await conn.execute(
            text("TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE")
        )
    yield eng
    await eng.dispose()


class _RecordingQueue:
    """Captures published messages so tests can assert without a real broker."""

    def __init__(self) -> None:
        self.messages: list[MatchJob] = []
        self.build_pod_messages: list[BuildPodMessage] = []

    async def enqueue_match(self, job: MatchJob) -> None:
        self.messages.append(job)

    async def enqueue_build_pod(self, msg: BuildPodMessage) -> None:
        self.build_pod_messages.append(msg)


@pytest.fixture()
def mock_queue() -> Iterator[_RecordingQueue]:
    queue = _RecordingQueue()
    web.main.app.dependency_overrides[get_queue] = lambda: queue
    yield queue
    web.main.app.dependency_overrides.clear()


@pytest.fixture()
def client(engine, mock_queue):
    db.session.reconfigure(TEST_ASYNC_URL)

    with TestClient(web.main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Seed helpers — used by tests to set up DB state.
# ---------------------------------------------------------------------------


async def _async_session_for(engine: AsyncEngine):
    """Return a fresh `AsyncSession` factory bound to the given engine.

    Each helper opens its own session so it doesn't tangle with whatever
    session the test under examination is using."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def db_insert_bot(
    engine: AsyncEngine,
    base_name: str,
    submitted_at: str | None = None,
    python_version: str = "3",
    version: int = 1,
    versioned_name: str | None = None,
    runtime_key: str | None = None,
) -> int:
    from web.runtimes import DEFAULT_RUNTIME_KEY, RUNTIMES

    if runtime_key is None:
        candidate = f"python-{python_version}"
        runtime_key = candidate if candidate in RUNTIMES else DEFAULT_RUNTIME_KEY
    factory = await _async_session_for(engine)
    async with factory() as session:
        bot = Bot(
            base_name=base_name,
            versioned_name=versioned_name if versioned_name is not None else base_name,
            version=version,
            owner_token="token",
            python_version=python_version,
            runtime_key=runtime_key,
        )
        if submitted_at is not None:
            bot.submitted_at = datetime.fromisoformat(submitted_at)
        session.add(bot)
        await session.flush()
        bot_id = bot.id
        await session.commit()
        return bot_id


async def db_insert_match(
    engine: AsyncEngine,
    bot_x_id: int,
    bot_o_id: int,
    winner_id: int | None,
    result: str,
    played_at: str | None = None,
) -> int:
    factory = await _async_session_for(engine)
    async with factory() as session:
        match = Match(
            bot_x_id=bot_x_id,
            bot_o_id=bot_o_id,
            winner_id=winner_id,
            result=result,
        )
        if played_at is not None:
            match.played_at = datetime.fromisoformat(played_at)
        session.add(match)
        await session.flush()
        match_id = match.id
        await session.commit()
        return match_id


async def db_insert_move(
    engine: AsyncEngine,
    match_id: int,
    move_number: int,
    bot_id: int,
    board_state: str,
    error: str | None = None,
) -> None:
    factory = await _async_session_for(engine)
    async with factory() as session:
        session.add(
            Move(
                match_id=match_id,
                move_number=move_number,
                bot_id=bot_id,
                board_state=board_state,
                error=error,
            )
        )
        await session.commit()
