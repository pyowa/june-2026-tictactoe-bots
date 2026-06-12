"""Unit tests for dispatcher/pods.py — pure k8s Pod lifecycle helpers."""

import json
from unittest.mock import MagicMock, patch

import pytest

from dispatcher.pods import (
    NAMESPACE,
    TURN_PORT,
    build_bot_pod_manifest,
    delete_pod,
    get_pod_ip,
    request_turn,
    wait_for_http_ready,
    wait_for_pod_ready,
)

# ---------------------------------------------------------------------------
# build_bot_pod_manifest — pure function, no mocks
# ---------------------------------------------------------------------------


def test_build_bot_pod_manifest_kind_and_api_version() -> None:
    m = build_bot_pod_manifest(
        "bot-42", "pyowa/bot-runner-python:3.14", "c3Jj", 42
    )
    assert m["apiVersion"] == "v1"
    assert m["kind"] == "Pod"


def test_build_bot_pod_manifest_metadata() -> None:
    m = build_bot_pod_manifest(
        "bot-42", "pyowa/bot-runner-python:3.14", "c3Jj", 42
    )
    assert m["metadata"]["name"] == "bot-42"
    assert m["metadata"]["namespace"] == NAMESPACE
    assert m["metadata"]["labels"]["app"] == "bot-runner"
    assert m["metadata"]["labels"]["bot-id"] == "42"
    assert "match-id" not in m["metadata"]["labels"]


def test_build_bot_pod_manifest_restart_policy_never() -> None:
    m = build_bot_pod_manifest("bot-42", "img", "src", 42)
    assert m["spec"]["restartPolicy"] == "Never"


def test_build_bot_pod_manifest_disables_service_account_token() -> None:
    m = build_bot_pod_manifest("bot-42", "img", "src", 42)
    assert m["spec"]["automountServiceAccountToken"] is False


def test_build_bot_pod_manifest_container_image_and_pull_policy() -> None:
    m = build_bot_pod_manifest("bot-42", "pyowa/bot-runner-python:3.13", "src", 42)
    container = m["spec"]["containers"][0]
    assert container["image"] == "pyowa/bot-runner-python:3.13"
    assert container["imagePullPolicy"] == "Never"


def test_build_bot_pod_manifest_source_b64_env_var() -> None:
    m = build_bot_pod_manifest("bot-42", "img", "my-source-b64", 42)
    container = m["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["SOURCE_B64"] == "my-source-b64"


def test_build_bot_pod_manifest_resources() -> None:
    m = build_bot_pod_manifest("bot-42", "img", "src", 42)
    resources = m["spec"]["containers"][0]["resources"]
    assert resources["limits"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "256Mi"
    assert resources["requests"]["cpu"] == "100m"
    assert resources["requests"]["memory"] == "64Mi"


def test_build_bot_pod_manifest_container_port() -> None:
    m = build_bot_pod_manifest("bot-42", "img", "src", 42)
    container = m["spec"]["containers"][0]
    ports = container["ports"]
    assert any(p["containerPort"] == 8080 for p in ports)


def test_build_bot_pod_manifest_no_readiness_probe() -> None:
    m = build_bot_pod_manifest("bot-42", "img", "src", 42)
    container = m["spec"]["containers"][0]
    assert "readinessProbe" not in container


# ---------------------------------------------------------------------------
# wait_for_pod_ready
# ---------------------------------------------------------------------------


def _make_pod_mock(phase: str, ready: bool) -> MagicMock:
    pod = MagicMock()
    pod.status.phase = phase
    status = MagicMock()
    status.ready = ready
    pod.status.container_statuses = [status]
    return pod


def test_wait_for_pod_ready_returns_when_running_and_ready() -> None:
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = _make_pod_mock("Running", True)
    wait_for_pod_ready(core_v1, "bot-x-abc")  # must not raise


def test_wait_for_pod_ready_raises_runtime_error_on_failed_phase() -> None:
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = _make_pod_mock("Failed", False)
    with pytest.raises(RuntimeError):
        wait_for_pod_ready(core_v1, "bot-x-abc")


def test_wait_for_pod_ready_raises_runtime_error_on_unknown_phase() -> None:
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = _make_pod_mock("Unknown", False)
    with pytest.raises(RuntimeError):
        wait_for_pod_ready(core_v1, "bot-x-abc")


def test_wait_for_pod_ready_raises_timeout_when_never_ready() -> None:
    core_v1 = MagicMock()
    # Running but not ready — never becomes ready
    core_v1.read_namespaced_pod.return_value = _make_pod_mock("Running", False)
    with pytest.raises(TimeoutError):
        wait_for_pod_ready(core_v1, "bot-x-abc", timeout=0.05)


def test_wait_for_pod_ready_polls_correct_name_and_namespace() -> None:
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = _make_pod_mock("Running", True)
    wait_for_pod_ready(core_v1, "bot-x-xyz")
    core_v1.read_namespaced_pod.assert_called_with("bot-x-xyz", NAMESPACE)


# ---------------------------------------------------------------------------
# wait_for_http_ready
# ---------------------------------------------------------------------------


def test_wait_for_http_ready_returns_when_urlopen_succeeds() -> None:
    response_mock = MagicMock()
    response_mock.__enter__ = lambda s: s
    response_mock.__exit__ = MagicMock(return_value=False)

    with patch("dispatcher.pods.urlopen", return_value=response_mock):
        wait_for_http_ready("10.0.0.5")  # must not raise


def test_wait_for_http_ready_calls_correct_url() -> None:
    response_mock = MagicMock()
    response_mock.__enter__ = lambda s: s
    response_mock.__exit__ = MagicMock(return_value=False)

    with patch("dispatcher.pods.urlopen", return_value=response_mock) as mock_urlopen:
        wait_for_http_ready("10.0.0.5")

    called_url = mock_urlopen.call_args[0][0]
    assert called_url == f"http://10.0.0.5:{TURN_PORT}/health"


def test_wait_for_http_ready_raises_timeout_when_urlopen_always_raises() -> None:
    with patch("dispatcher.pods.urlopen", side_effect=OSError("connection refused")):
        with pytest.raises(TimeoutError):
            wait_for_http_ready("10.0.0.5", timeout=0.05)


def test_wait_for_http_ready_succeeds_on_second_attempt() -> None:
    response_mock = MagicMock()
    response_mock.__enter__ = lambda s: s
    response_mock.__exit__ = MagicMock(return_value=False)

    side_effects = [OSError("not yet"), response_mock]
    with patch("dispatcher.pods.urlopen", side_effect=side_effects):
        wait_for_http_ready("10.0.0.5")  # must not raise


# ---------------------------------------------------------------------------
# get_pod_ip
# ---------------------------------------------------------------------------


def test_get_pod_ip_returns_ip() -> None:
    core_v1 = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.5"
    core_v1.read_namespaced_pod.return_value = pod
    ip = get_pod_ip(core_v1, "bot-x-abc")
    assert ip == "10.0.0.5"


def test_get_pod_ip_raises_if_empty() -> None:
    core_v1 = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = ""
    core_v1.read_namespaced_pod.return_value = pod
    with pytest.raises(RuntimeError):
        get_pod_ip(core_v1, "bot-x-abc")


def test_get_pod_ip_raises_if_none() -> None:
    core_v1 = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = None
    core_v1.read_namespaced_pod.return_value = pod
    with pytest.raises(RuntimeError):
        get_pod_ip(core_v1, "bot-x-abc")


def test_get_pod_ip_reads_correct_name_and_namespace() -> None:
    core_v1 = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.1"
    core_v1.read_namespaced_pod.return_value = pod
    get_pod_ip(core_v1, "bot-o-xyz")
    core_v1.read_namespaced_pod.assert_called_with("bot-o-xyz", NAMESPACE)


# ---------------------------------------------------------------------------
# request_turn
# ---------------------------------------------------------------------------


def test_request_turn_posts_json_to_correct_url() -> None:
    response_mock = MagicMock()
    response_mock.__enter__ = lambda s: s
    response_mock.__exit__ = MagicMock(return_value=False)
    board_json = json.dumps({"board": "X|.|.\n.|.|.\n.|.|."}).encode()
    response_mock.read.return_value = board_json

    with patch("dispatcher.pods.urlopen", return_value=response_mock) as mock_urlopen:
        request_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")

    call_args = mock_urlopen.call_args
    req = call_args[0][0]
    assert req.full_url == f"http://10.0.0.5:{TURN_PORT}/turn"
    assert req.get_method() == "POST"
    body = json.loads(req.data)
    assert body["symbol"] == "X"
    assert body["board"] == ".|.|.\n.|.|.\n.|.|."


def test_request_turn_returns_parsed_json() -> None:
    response_mock = MagicMock()
    response_mock.__enter__ = lambda s: s
    response_mock.__exit__ = MagicMock(return_value=False)
    board_json = json.dumps({"board": "X|.|.\n.|.|.\n.|.|."}).encode()
    response_mock.read.return_value = board_json

    with patch("dispatcher.pods.urlopen", return_value=response_mock):
        result = request_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")

    assert result == {"board": "X|.|.\n.|.|.\n.|.|."}


def test_request_turn_with_error_response() -> None:
    response_mock = MagicMock()
    response_mock.__enter__ = lambda s: s
    response_mock.__exit__ = MagicMock(return_value=False)
    response_mock.read.return_value = json.dumps({"error": "runtime crash"}).encode()

    with patch("dispatcher.pods.urlopen", return_value=response_mock):
        result = request_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")

    assert result == {"error": "runtime crash"}


# ---------------------------------------------------------------------------
# delete_pod
# ---------------------------------------------------------------------------


def test_delete_pod_calls_k8s_delete() -> None:
    core_v1 = MagicMock()
    delete_pod(core_v1, "bot-x-abc")
    core_v1.delete_namespaced_pod.assert_called_once_with("bot-x-abc", NAMESPACE)
