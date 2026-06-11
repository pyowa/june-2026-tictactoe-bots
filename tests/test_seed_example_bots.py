from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import db.session as d
import scripts.seed_example_bots as seed
from db.session import get_session
from entities.bot.model import Bot
from entities.bot.repository import BotRepository
from scripts.seed_example_bots import enqueue_all_pairs, main
from tests.conftest import TEST_ASYNC_URL, _RecordingQueue, db_insert_bot


@pytest_asyncio.fixture()
async def _bound_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """Bind the async DB engine to the test Postgres so seed_example_bots.main()
    sees the test database via `get_session()`."""
    d.reconfigure(TEST_ASYNC_URL)
    yield


# ---------------------------------------------------------------------------
# enqueue_all_pairs
# ---------------------------------------------------------------------------


async def test_enqueue_all_pairs_emits_n_squared_jobs_with_max_python_version(
    engine: AsyncEngine, mock_queue: _RecordingQueue, _bound_db: None
) -> None:
    a = await db_insert_bot(engine, "Alpha", python_version="3.11")
    b = await db_insert_bot(engine, "Beta", python_version="3.13")
    c = await db_insert_bot(engine, "Gamma", python_version="3.12")

    async with get_session() as session:
        count = await enqueue_all_pairs(BotRepository(session), mock_queue)

    assert count == 9  # 3 bots -> 3*3 ordered pairs (self-pairs included)
    assert len(mock_queue.messages) == 9

    # Each MatchJob's python_version must be max(x_py, o_py).
    by_id = {a: "3.11", b: "3.13", c: "3.12"}
    for job in mock_queue.messages:
        expected = max(
            by_id[job.bot_x_id],
            by_id[job.bot_o_id],
            key=lambda v: tuple(int(p) for p in v.split(".")),
        )
        assert job.python_version == expected

    # Self-pairs are included.
    self_pairs = [j for j in mock_queue.messages if j.bot_x_id == j.bot_o_id]
    assert len(self_pairs) == 3


async def test_enqueue_all_pairs_with_no_bots_emits_nothing(
    engine: AsyncEngine, mock_queue: _RecordingQueue, _bound_db: None
) -> None:
    async with get_session() as session:
        count = await enqueue_all_pairs(BotRepository(session), mock_queue)
    assert count == 0
    assert mock_queue.messages == []


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _write_bot(dir_: Path, filename: str, docstring_body: str, extra: str = "") -> None:
    content = f'"""\n{docstring_body}\n"""\n{extra}'
    (dir_ / filename).write_text(content)


async def test_main_inserts_bots_and_enqueues_match_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    mock_queue: _RecordingQueue,
    _bound_db: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_bot(tmp_path, "alpha.py", "name: Alpha\npython: 3.11")
    _write_bot(tmp_path, "beta.py", "name: Beta")  # default python version
    monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)
    monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)

    await main()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    Bot.base_name,
                    Bot.versioned_name,
                    Bot.version,
                    Bot.python_version,
                ).order_by(Bot.base_name)
            )
        ).all()

    # Both bots inserted with v1 and the expected python_version.
    assert len(rows) == 2
    by_name = {r[0]: r for r in rows}
    assert by_name["Alpha"][1] == "Alpha"
    assert by_name["Alpha"][2] == 1
    assert by_name["Alpha"][3] == "3.11"
    assert by_name["Beta"][1] == "Beta"
    assert by_name["Beta"][2] == 1
    # Default python version when no `python:` field — matches web.utils' default.
    from web.utils import DEFAULT_PYTHON_VERSION

    assert by_name["Beta"][3] == DEFAULT_PYTHON_VERSION

    # 2 bots -> 2*2 = 4 MatchJobs enqueued.
    assert len(mock_queue.messages) == 4

    out = capsys.readouterr().out
    assert "Inserted 2 bots" in out
    assert "enqueued 4 match jobs" in out


async def test_main_auto_versions_duplicate_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    mock_queue: _RecordingQueue,
    _bound_db: None,
) -> None:
    # Two files share `name: Foo` -> v1 (Foo) and v2 (FooV2).
    _write_bot(tmp_path, "foo_a.py", "name: Foo")
    _write_bot(tmp_path, "foo_b.py", "name: Foo")
    monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)
    monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)

    await main()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Bot.versioned_name, Bot.version)
                .where(Bot.base_name == "Foo")
                .order_by(Bot.version)
            )
        ).all()
    assert [(r[0], r[1]) for r in rows] == [("Foo", 1), ("FooV2", 2)]


async def test_main_skips_files_without_name_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    mock_queue: _RecordingQueue,
    _bound_db: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A docstring without `name:` is skipped.
    _write_bot(tmp_path, "nameless.py", "no name here")
    _write_bot(tmp_path, "good.py", "name: Good")
    monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)
    monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)

    await main()

    out = capsys.readouterr().out
    assert "Skipping nameless.py" in out
    assert "no 'name:' field" in out

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        names = [r[0] for r in (await session.execute(select(Bot.base_name))).all()]
    assert names == ["Good"]
    # Only one bot inserted -> 1*1 = 1 match job.
    assert len(mock_queue.messages) == 1


async def test_main_falls_back_to_python_3_when_version_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    mock_queue: _RecordingQueue,
    _bound_db: None,
) -> None:
    """A bot declaring `python: 9.99` is unparseable/unsupported, so
    `extract_python_version` returns None. The seed script must fall back
    to "3" rather than persisting NULL or crashing."""
    _write_bot(tmp_path, "weird.py", "name: Weird\npython: 9.99")
    monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)
    monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)

    await main()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(Bot.python_version).where(Bot.base_name == "Weird")
            )
        ).one()
    assert row[0] == "3"


async def test_main_with_empty_directory_prints_and_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    mock_queue: _RecordingQueue,
    _bound_db: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)
    monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)

    await main()

    out = capsys.readouterr().out
    assert "No .py files found" in out

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        count = (await session.execute(select(func.count()).select_from(Bot))).scalar()
    assert count == 0
    assert mock_queue.messages == []
