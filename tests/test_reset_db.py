from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

import db.session as d
import scripts.reset_db as reset_db
from db.base import Base
from scripts.reset_db import main
from tests.conftest import TEST_ASYNC_URL
from tests.test_purge_rabbitmq import _FakeResponse


@pytest_asyncio.fixture()
async def _bound_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """Bind the async DB engine to the test Postgres so reset_db points at
    `ttt_test`. After the test, re-create the schema we may have dropped
    so later tests still see tables."""
    d.reconfigure(TEST_ASYNC_URL)
    yield
    # The test session-scoped fixture only creates the schema once; if main()
    # dropped the tables we must recreate them so subsequent tests have a DB.
    eng = create_async_engine(TEST_ASYNC_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


async def test_main_drops_tables_runs_alembic_and_purges_queues(
    monkeypatch: pytest.MonkeyPatch,
    engine: AsyncEngine,
    _bound_db: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Patch subprocess.run so alembic and kubectl don't actually fire.
    subprocess_calls: list[Any] = []

    def fake_subprocess_run(*args: Any, **kwargs: Any) -> MagicMock:
        subprocess_calls.append((args, kwargs))
        return MagicMock(returncode=0)

    monkeypatch.setattr(reset_db.subprocess, "run", fake_subprocess_run)

    # Patch urlopen so purge_rabbitmq_queues() finds zero queues.
    def fake_urlopen(req: Any, timeout: float = 5) -> _FakeResponse:
        return _FakeResponse([])

    monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)

    await main()

    # Tables really got dropped.
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        present = (
            await session.execute(
                select(
                    func.to_regclass("public.bots"),
                    func.to_regclass("public.matches"),
                    func.to_regclass("public.moves"),
                    func.to_regclass("public.alembic_version"),
                )
            )
        ).one()
    assert present is not None
    assert all(v is None for v in present)

    # alembic and kubectl both invoked.
    assert len(subprocess_calls) == 2
    alembic_args, alembic_kwargs = subprocess_calls[0]
    assert alembic_args[0] == ["alembic", "upgrade", "head"]
    assert alembic_kwargs.get("check") is True
    kubectl_args, _ = subprocess_calls[1]
    assert kubectl_args[0] == ["kubectl", "delete", "pods", "--all", "-n", "bots"]

    out = capsys.readouterr().out
    assert "Dropping all tables" in out
    assert "Running migrations" in out
    assert "Purging RabbitMQ queues" in out
    assert "Deleting bot pods" in out
    assert "Done." in out
