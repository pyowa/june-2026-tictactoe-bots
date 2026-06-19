"""Unit tests for scripts/reset_db.py — purge_rabbitmq_queues and helpers."""

import io
import json
import urllib.error
from email.message import Message
from typing import Any

import pytest

import scripts.reset_db as reset_db
from scripts.reset_db import purge_rabbitmq_queues


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
