import io
import json
import subprocess
import urllib.error
from collections.abc import AsyncIterator
from email.message import Message
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
from scripts.reset_db import main, purge_rabbitmq_queues
from tests.conftest import TEST_ASYNC_URL


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


class _FakeResponse:
    """Minimal context-manager response: supports `.read()` returning the
    configured JSON body. Used to fake `urllib.request.urlopen` returns for
    the management API GET call."""

    def __init__(self, payload: Any) -> None:
        self._payload = json.dumps(payload).encode()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://localhost:15672/api/queues/foo/contents",
        code=code,
        msg="boom",
        hdrs=Message(),
        fp=io.BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# purge_rabbitmq_queues
# ---------------------------------------------------------------------------


def test_purge_normal_queues_issues_delete_and_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queues = [
        {"name": "matches.todo", "vhost": "/", "messages": 5},
        {"name": "turn.py3.requests", "vhost": "/", "messages": 0},
    ]
    requests: list[tuple[str, str | None]] = []

    def fake_urlopen(req: Any, timeout: float = 5) -> _FakeResponse:
        method = getattr(req, "method", None) or req.get_method()
        requests.append((req.full_url, method))
        if method == "DELETE":
            return _FakeResponse({})
        return _FakeResponse(queues)

    monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)

    purge_rabbitmq_queues()

    # One GET + two DELETEs.
    methods = [m for _, m in requests]
    assert methods.count("GET") == 1
    assert methods.count("DELETE") == 2

    out = capsys.readouterr().out
    assert "Purged queue: matches.todo (5 messages)" in out
    assert "Purged queue: turn.py3.requests (0 messages)" in out
    assert "Purged 2 queue(s)." in out


def test_purge_skips_amq_prefixed_queues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queues = [
        {"name": "amq.gen-abc", "vhost": "/", "messages": 0},
        {"name": "amq_reply.xyz", "vhost": "/", "messages": 0},
        {"name": "matches.todo", "vhost": "/", "messages": 0},
    ]
    delete_targets: list[str] = []

    def fake_urlopen(req: Any, timeout: float = 5) -> _FakeResponse:
        method = getattr(req, "method", None) or req.get_method()
        if method == "DELETE":
            delete_targets.append(req.full_url)
            return _FakeResponse({})
        return _FakeResponse(queues)

    monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)

    purge_rabbitmq_queues()

    # Only matches.todo gets a DELETE.
    assert len(delete_targets) == 1
    assert "matches.todo" in delete_targets[0]
    assert "Purged 1 queue(s)." in capsys.readouterr().out


def test_purge_tolerates_404_on_delete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queues = [{"name": "matches.todo", "vhost": "/", "messages": 0}]

    def fake_urlopen(req: Any, timeout: float = 5) -> _FakeResponse:
        method = getattr(req, "method", None) or req.get_method()
        if method == "DELETE":
            raise _http_error(404)
        return _FakeResponse(queues)

    monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)

    purge_rabbitmq_queues()

    out = capsys.readouterr().out
    # 404 should not print a "Failed to purge" message.
    assert "Failed to purge" not in out
    # The counter doesn't increment when DELETE raises.
    assert "Purged 0 queue(s)." in out


def test_purge_reports_non_404_http_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queues = [{"name": "matches.todo", "vhost": "/", "messages": 0}]

    def fake_urlopen(req: Any, timeout: float = 5) -> _FakeResponse:
        method = getattr(req, "method", None) or req.get_method()
        if method == "DELETE":
            raise _http_error(500)
        return _FakeResponse(queues)

    monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)

    purge_rabbitmq_queues()

    out = capsys.readouterr().out
    assert "Failed to purge matches.todo" in out


def test_purge_handles_url_error_on_get(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_urlopen(req: Any, timeout: float = 5) -> _FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)

    purge_rabbitmq_queues()

    out = capsys.readouterr().out
    assert "RabbitMQ management API unreachable" in out


def test_purge_handles_timeout_error_on_get(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_urlopen(req: Any, timeout: float = 5) -> _FakeResponse:
        raise TimeoutError("slow")

    monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)

    purge_rabbitmq_queues()

    out = capsys.readouterr().out
    assert "RabbitMQ management API unreachable" in out


# ---------------------------------------------------------------------------
# delete_bot_pods
# ---------------------------------------------------------------------------


def test_delete_bot_pods_calls_kubectl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.reset_db import delete_bot_pods

    calls: list[Any] = []

    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        calls.append(args[0])
        return MagicMock(returncode=0)

    monkeypatch.setattr(reset_db.subprocess, "run", fake_run)
    delete_bot_pods()

    assert calls == [["kubectl", "delete", "pods", "--all", "-n", "bots"]]
    assert "Deleted bot pods" in capsys.readouterr().out


def test_delete_bot_pods_skips_when_kubectl_not_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.reset_db import delete_bot_pods

    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        raise FileNotFoundError("kubectl not found")

    monkeypatch.setattr(reset_db.subprocess, "run", fake_run)
    delete_bot_pods()

    assert "kubectl not found" in capsys.readouterr().out


def test_delete_bot_pods_skips_when_cluster_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.reset_db import delete_bot_pods

    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        raise subprocess.CalledProcessError(1, "kubectl")

    monkeypatch.setattr(reset_db.subprocess, "run", fake_run)
    delete_bot_pods()

    assert "No k8s cluster reachable" in capsys.readouterr().out


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
