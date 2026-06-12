from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import db.session as d
import scripts.seed_example_bots as seed
from entities.bot.model import Bot
from scripts.seed_example_bots import main
from tests.conftest import TEST_ASYNC_URL, _RecordingQueue


@pytest_asyncio.fixture()
async def _bound_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """Bind the async DB engine to the test Postgres so seed_example_bots.main()
    sees the test database via `get_session()`."""
    d.reconfigure(TEST_ASYNC_URL)
    yield


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _write_bot(dir_: Path, filename: str, docstring_body: str, extra: str = "") -> None:
    content = f'"""\n{docstring_body}\n"""\n{extra}'
    (dir_ / filename).write_text(content)


async def test_main_inserts_bots_and_enqueues_build_pod_jobs(
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

    # One BuildPodMessage per bot.
    assert len(mock_queue.build_pod_messages) == 2

    out = capsys.readouterr().out
    assert "Inserted 2 bots" in out
    assert "enqueued 2 build-pod jobs" in out
    assert "matches.build" in out


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
    # One BuildPodMessage per bot.
    assert len(mock_queue.build_pod_messages) == 2


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
    # Only one bot inserted -> one BuildPodMessage.
    assert len(mock_queue.build_pod_messages) == 1


async def test_main_falls_back_to_default_runtime_when_version_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    mock_queue: _RecordingQueue,
    _bound_db: None,
) -> None:
    """A bot declaring `python: 9.99` is unsupported, so `extract_runtime_key`
    returns None. The seed script must fall back to DEFAULT_RUNTIME_KEY rather
    than persisting NULL or crashing."""
    from web.runtimes import DEFAULT_RUNTIME_KEY
    from web.utils import DEFAULT_PYTHON_VERSION

    _write_bot(tmp_path, "weird.py", "name: Weird\npython: 9.99")
    monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)
    monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)

    await main()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(Bot.python_version, Bot.runtime_key).where(
                    Bot.base_name == "Weird"
                )
            )
        ).one()
    assert row[0] == DEFAULT_PYTHON_VERSION
    assert row[1] == DEFAULT_RUNTIME_KEY
    # BuildPodMessage uses the default runtime key.
    assert len(mock_queue.build_pod_messages) == 1
    assert mock_queue.build_pod_messages[0].runtime_key == DEFAULT_RUNTIME_KEY


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
    assert mock_queue.build_pod_messages == []


async def test_main_build_pod_messages_have_correct_bot_ids_and_runtime_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    mock_queue: _RecordingQueue,
    _bound_db: None,
) -> None:
    """Each BuildPodMessage must carry the correct bot_id and runtime_key."""
    from web.runtimes import RUNTIMES

    # Pick two runtime keys that actually exist.
    rk_list = sorted(RUNTIMES.keys())
    rk_a, rk_b = rk_list[0], rk_list[-1]
    py_a = rk_a.split("-", 1)[1]  # e.g. "python-3.11" -> "3.11"
    py_b = rk_b.split("-", 1)[1]

    _write_bot(tmp_path, "aa.py", f"name: BotA\npython: {py_a}")
    _write_bot(tmp_path, "bb.py", f"name: BotB\npython: {py_b}")
    monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)
    monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)

    await main()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Bot.id, Bot.base_name, Bot.runtime_key).order_by(Bot.base_name)
            )
        ).all()

    by_name = {r[1]: (r[0], r[2]) for r in rows}
    msgs_by_id = {m.bot_id: m for m in mock_queue.build_pod_messages}

    assert msgs_by_id[by_name["BotA"][0]].runtime_key == by_name["BotA"][1]
    assert msgs_by_id[by_name["BotB"][0]].runtime_key == by_name["BotB"][1]
