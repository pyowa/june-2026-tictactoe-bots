"""Unit tests for `web/bot_client.py` — k8s pod lookup + bot HTTP call.

All k8s and HTTP I/O is mocked. Cover the happy path plus every named
failure mode: missing pod, timeout, invalid response body."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from web.bot_client import (
    NAMESPACE,
    BotForfeit,
    get_pod_ip,
    request_bot_turn,
)

# ---------------------------------------------------------------------------
# get_pod_ip — returns IP or None for missing pods
# ---------------------------------------------------------------------------


def test_get_pod_ip_returns_ip() -> None:
    core_v1 = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.5"
    core_v1.read_namespaced_pod.return_value = pod
    assert get_pod_ip(core_v1, "bot-42") == "10.0.0.5"


def test_get_pod_ip_returns_none_when_pod_missing() -> None:
    """A 404 from the k8s API means the pod no longer exists."""
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.side_effect = ApiException(status=404)
    assert get_pod_ip(core_v1, "bot-42") is None


def test_get_pod_ip_returns_none_when_pod_has_no_ip() -> None:
    """A pod that exists but hasn't been assigned an IP yet — treat as missing."""
    core_v1 = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = None
    core_v1.read_namespaced_pod.return_value = pod
    assert get_pod_ip(core_v1, "bot-42") is None


def test_get_pod_ip_uses_bots_namespace() -> None:
    core_v1 = MagicMock()
    pod = MagicMock()
    pod.status.pod_ip = "10.0.0.5"
    core_v1.read_namespaced_pod.return_value = pod
    get_pod_ip(core_v1, "bot-42")
    core_v1.read_namespaced_pod.assert_called_once_with("bot-42", NAMESPACE)


def test_namespace_is_bots() -> None:
    """The namespace constant must be 'bots' — that's where pod_builder
    creates bot pods. Anchoring this keeps Role/RoleBinding YAML in sync."""
    assert NAMESPACE == "bots"


# ---------------------------------------------------------------------------
# request_bot_turn — POSTs to the pod, returns its new board
# ---------------------------------------------------------------------------


def _make_resp(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = body
    return resp


def test_request_bot_turn_returns_parsed_board() -> None:
    resp = _make_resp(json.dumps({"board": "X|O|.\n.|.|.\n.|.|."}).encode())
    with patch("web.bot_client.urlopen", return_value=resp):
        out = request_bot_turn("10.0.0.5", "O", "X|.|.\n.|.|.\n.|.|.")
    assert out == "X|O|.\n.|.|.\n.|.|."


def test_request_bot_turn_posts_correct_url_and_body() -> None:
    resp = _make_resp(json.dumps({"board": "X|O|.\n.|.|.\n.|.|."}).encode())
    with patch("web.bot_client.urlopen", return_value=resp) as mock:
        request_bot_turn("10.0.0.5", "O", "X|.|.\n.|.|.\n.|.|.")
    req = mock.call_args[0][0]
    assert req.full_url == "http://10.0.0.5:8080/turn"
    assert req.get_method() == "POST"
    body = json.loads(req.data)
    assert body == {"symbol": "O", "board": "X|.|.\n.|.|.\n.|.|."}


def test_request_bot_turn_passes_timeout_to_urlopen() -> None:
    resp = _make_resp(json.dumps({"board": "X|.|.\n.|.|.\n.|.|."}).encode())
    with patch("web.bot_client.urlopen", return_value=resp) as mock:
        request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.", timeout=7.5)
    assert mock.call_args.kwargs["timeout"] == 7.5


def test_request_bot_turn_timeout_raises_bot_forfeit_took_too_long() -> None:
    """A socket timeout from urlopen is translated to a friendly forfeit."""
    with patch(
        "web.bot_client.urlopen", side_effect=TimeoutError("read timed out")
    ):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.", timeout=0.1)
    assert exc.value.reason == "Bot took too long"


def test_request_bot_turn_url_error_raises_bot_unavailable() -> None:
    """A connection error means the bot pod is gone or unreachable."""
    with patch(
        "web.bot_client.urlopen", side_effect=URLError("connection refused")
    ):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot is unavailable"


def test_request_bot_turn_unparseable_response_raises_invalid_move() -> None:
    resp = _make_resp(b"not json at all")
    with patch("web.bot_client.urlopen", return_value=resp):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot returned an invalid move"


def test_request_bot_turn_missing_board_field_raises_invalid_move() -> None:
    """Response that parses as JSON but has no 'board' key (e.g. just an error)."""
    resp = _make_resp(json.dumps({"error": "syntax error"}).encode())
    with patch("web.bot_client.urlopen", return_value=resp):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot returned an invalid move"


def test_request_bot_turn_unparseable_board_string_raises_invalid_move() -> None:
    """Response with a 'board' field that can't be parsed as a 3x3 board."""
    resp = _make_resp(json.dumps({"board": "garbage"}).encode())
    with patch("web.bot_client.urlopen", return_value=resp):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot returned an invalid move"


def test_request_bot_turn_row_with_bad_cell_raises_invalid_move() -> None:
    """Three rows but one row has a non-XO. character: invalid move."""
    resp = _make_resp(json.dumps({"board": "X|Z|.\n.|.|.\n.|.|."}).encode())
    with patch("web.bot_client.urlopen", return_value=resp):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot returned an invalid move"


def test_request_bot_turn_row_with_wrong_cell_count_raises_invalid_move() -> None:
    """Three rows but one row has only two cells: invalid move."""
    resp = _make_resp(json.dumps({"board": ".|.|.\n.|.\n.|.|."}).encode())
    with patch("web.bot_client.urlopen", return_value=resp):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot returned an invalid move"


def test_request_bot_turn_illegal_move_raises_invalid_move() -> None:
    """Bot put its symbol in two cells, or overwrote one of ours."""
    # Old: empty board. New: bot claims it placed two Xs.
    resp = _make_resp(json.dumps({"board": "X|X|.\n.|.|.\n.|.|."}).encode())
    with patch("web.bot_client.urlopen", return_value=resp):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "X", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot returned an invalid move"


def test_request_bot_turn_wrong_symbol_raises_invalid_move() -> None:
    """Bot was asked to play O but its diff shows an X — invalid."""
    resp = _make_resp(json.dumps({"board": "X|.|.\n.|.|.\n.|.|."}).encode())
    with patch("web.bot_client.urlopen", return_value=resp):
        with pytest.raises(BotForfeit) as exc:
            request_bot_turn("10.0.0.5", "O", ".|.|.\n.|.|.\n.|.|.")
    assert exc.value.reason == "Bot returned an invalid move"


# ---------------------------------------------------------------------------
# BotForfeit.reason — round-trip
# ---------------------------------------------------------------------------


def test_bot_forfeit_reason_round_trip() -> None:
    """BotForfeit carries its reason on the .reason attribute (not just str())."""
    err = BotForfeit("Bot is unavailable")
    assert err.reason == "Bot is unavailable"
    assert "Bot is unavailable" in str(err)
