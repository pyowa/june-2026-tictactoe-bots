"""Unit tests for scripts/reset_db.py — delete_bot_pods."""

import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest

import scripts.reset_db as reset_db
from scripts.reset_db import delete_bot_pods


def test_delete_bot_pods_calls_kubectl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        raise FileNotFoundError("kubectl not found")

    monkeypatch.setattr(reset_db.subprocess, "run", fake_run)
    delete_bot_pods()

    assert "kubectl not found" in capsys.readouterr().out


def test_delete_bot_pods_skips_when_cluster_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        raise subprocess.CalledProcessError(1, "kubectl")

    monkeypatch.setattr(reset_db.subprocess, "run", fake_run)
    delete_bot_pods()

    assert "No k8s cluster reachable" in capsys.readouterr().out
