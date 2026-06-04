import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

import db.database
import messaging.client
import web.main
from db.models.base import Base
from messaging.queue import MatchJob

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

_BASE_URL = f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}"
TEST_SYNC_URL = f"{_BASE_URL}/{TEST_DB_NAME}"
TEST_ASYNC_URL = TEST_SYNC_URL.replace("+psycopg2", "+asyncpg")


def _ensure_test_database_exists() -> None:
    """Connect to the admin DB and create `ttt_test` if it isn't there."""
    admin_engine = create_engine(
        f"{_BASE_URL}/{PG_ADMIN_DB}", isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": TEST_DB_NAME},
        ).first()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    """Create the test database (if needed) and apply the schema once."""
    _ensure_test_database_exists()
    engine = create_engine(TEST_SYNC_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# Per-test fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> Iterator[Engine]:
    """A sync engine bound to the test database. Truncates data tables
    before each test so tests don't see each other's rows."""
    eng = create_engine(TEST_SYNC_URL)
    with eng.begin() as conn:
        conn.execute(
            text("TRUNCATE bots, matches, moves RESTART IDENTITY CASCADE")
        )
    yield eng
    eng.dispose()


class _RecordingQueue:
    """Captures published `MatchJob`s so tests can assert without a real broker."""

    def __init__(self) -> None:
        self.messages: list[MatchJob] = []

    async def enqueue_match(self, job: MatchJob) -> None:
        self.messages.append(job)


@pytest.fixture()
def mock_queue() -> Iterator[_RecordingQueue]:
    queue = _RecordingQueue()
    messaging.client.set_queue(queue)
    yield queue
    messaging.client.set_queue(None)


@pytest.fixture()
def client(engine, mock_queue):
    db.database.reconfigure(TEST_ASYNC_URL)

    with TestClient(web.main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Seed helpers — used by tests to set up DB state.
# ---------------------------------------------------------------------------


def db_insert_bot(
    engine: Engine,
    base_name: str,
    submitted_at: str | None = None,
    python_version: str = "3",
) -> int:
    sql = text(
        """
        INSERT INTO bots
            (base_name, versioned_name, version, owner_token,
             python_version, submitted_at)
        VALUES (:bn, :bn, 1, 'token',
                :pv, COALESCE(CAST(:sa AS timestamp), CURRENT_TIMESTAMP))
        RETURNING id
        """
    )
    with engine.begin() as conn:
        result = conn.execute(
            sql,
            {
                "bn": base_name,
                "pv": python_version,
                "sa": submitted_at,
            },
        )
        return result.scalar_one()


def db_insert_match(
    engine: Engine,
    bot_x_id: int,
    bot_o_id: int,
    winner_id: int | None,
    result: str,
    played_at: str | None = None,
) -> int:
    sql = text(
        """
        INSERT INTO matches (bot_x_id, bot_o_id, winner_id, result, played_at)
        VALUES (:bx, :bo, :w, :r,
                COALESCE(CAST(:pa AS timestamp), CURRENT_TIMESTAMP))
        RETURNING id
        """
    )
    with engine.begin() as conn:
        res = conn.execute(
            sql,
            {
                "bx": bot_x_id,
                "bo": bot_o_id,
                "w": winner_id,
                "r": result,
                "pa": played_at,
            },
        )
        return res.scalar_one()


def db_insert_move(
    engine: Engine,
    match_id: int,
    move_number: int,
    bot_id: int,
    board_state: str,
    error: str | None = None,
) -> None:
    sql = text(
        """
        INSERT INTO moves (match_id, move_number, bot_id, board_state, error)
        VALUES (:m, :n, :b, :bs, :e)
        """
    )
    with engine.begin() as conn:
        conn.execute(
            sql,
            {
                "m": match_id,
                "n": move_number,
                "b": bot_id,
                "bs": board_state,
                "e": error,
            },
        )
