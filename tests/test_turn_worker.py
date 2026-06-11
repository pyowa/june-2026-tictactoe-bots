import base64
import json
import textwrap

from structlog.testing import capture_logs

import runner.turn_worker  # noqa: F401  -- smoke-import the entrypoint module so coverage sees its top-level imports
from runner.bot_subprocess import handle_turn, run_bot_subprocess

# ---------------------------------------------------------------------------
# run_bot_subprocess — actually invokes Python on a tmpfile.
# ---------------------------------------------------------------------------


SIMPLE_BOT = textwrap.dedent(
    """\
    import sys
    data = sys.stdin.read().strip().splitlines()
    symbol = data[0]
    board = [row.split('|') for row in data[1:]]
    for r in range(3):
        for c in range(3):
            if board[r][c] == '.':
                board[r][c] = symbol
                print('\\n'.join('|'.join(row) for row in board))
                sys.exit(0)
    """
).encode()


def test_run_bot_subprocess_returns_new_board() -> None:
    response = run_bot_subprocess(SIMPLE_BOT, "X", ".|.|.\n.|.|.\n.|.|.")
    assert response["error"] is None
    assert response["board"] == "X|.|.\n.|.|.\n.|.|."


def test_run_bot_subprocess_captures_stderr_on_empty_stdout() -> None:
    bot = b"import sys\nsys.stderr.write('boom\\n')\nsys.exit(1)\n"
    response = run_bot_subprocess(bot, "X", ".|.|.\n.|.|.\n.|.|.")
    assert response["board"] is None
    assert "empty response" in (response["error"] or "")
    assert "boom" in (response["error"] or "")


def test_run_bot_subprocess_empty_stdout_empty_stderr_no_trailing_colon() -> None:
    """When the bot writes nothing to stdout AND nothing to stderr, the
    error must be exactly `invalid output: empty response` — no trailing
    colon (the `detail = f": {stderr}" if stderr else ""` branch)."""
    bot = b"import sys\nsys.exit(0)\n"
    response = run_bot_subprocess(bot, "X", ".|.|.\n.|.|.\n.|.|.")
    assert response["board"] is None
    assert response["error"] == "invalid output: empty response"


def test_run_bot_subprocess_times_out() -> None:
    slow = b"import time\ntime.sleep(60)\n"
    response = run_bot_subprocess(slow, "X", ".|.|.\n.|.|.\n.|.|.", timeout=1)
    assert response["board"] is None
    assert "timeout" in (response["error"] or "")


def test_run_bot_subprocess_cleans_up_tmpfile_after_run() -> None:
    """The `finally: os.unlink(tmpfile_path)` block must remove the tmpfile
    even on a successful run. Capture the path NamedTemporaryFile produces,
    then assert it's gone."""
    import os
    import tempfile as real_tempfile
    from unittest.mock import patch

    captured_paths: list[str] = []
    real_factory = real_tempfile.NamedTemporaryFile

    def capturing_factory(*args, **kwargs):
        handle = real_factory(*args, **kwargs)
        captured_paths.append(handle.name)
        return handle

    with patch("runner.bot_subprocess.tempfile.NamedTemporaryFile", capturing_factory):
        run_bot_subprocess(SIMPLE_BOT, "X", ".|.|.\n.|.|.\n.|.|.")

    assert captured_paths, "expected NamedTemporaryFile to be called"
    for path in captured_paths:
        assert not os.path.exists(path), f"tmpfile {path} was not cleaned up"


def test_run_bot_subprocess_catches_unexpected_exception() -> None:
    """If `subprocess.run` raises something other than TimeoutExpired (e.g.
    OS-level issue), the worker should report it rather than crash."""
    from unittest.mock import patch

    def boom(*args, **kwargs):
        raise OSError("no such file or directory")

    with patch("runner.bot_subprocess.subprocess.run", side_effect=boom):
        response = run_bot_subprocess(b"# whatever", "X", ".|.|.\n.|.|.\n.|.|.")
    assert response["board"] is None
    assert "runtime error" in (response["error"] or "")


# ---------------------------------------------------------------------------
# handle_turn — payload + base64 decoding + JSON response shape
# ---------------------------------------------------------------------------


async def test_handle_turn_decodes_payload_and_returns_json() -> None:
    body = json.dumps(
        {
            "symbol": "X",
            "board": ".|.|.\n.|.|.\n.|.|.",
            "source_b64": base64.b64encode(SIMPLE_BOT).decode("ascii"),
        }
    ).encode()
    response_bytes = await handle_turn(body)
    response = json.loads(response_bytes)
    assert response["error"] is None
    assert response["board"] == "X|.|.\n.|.|.\n.|.|."


async def test_handle_turn_returns_worker_error_on_bad_payload() -> None:
    response = json.loads(await handle_turn(b"{not valid json"))
    assert response["board"] is None
    assert "worker error" in response["error"]


async def test_handle_turn_logs_turn_handled_on_success() -> None:
    body = json.dumps(
        {
            "symbol": "X",
            "board": ".|.|.\n.|.|.\n.|.|.",
            "source_b64": base64.b64encode(SIMPLE_BOT).decode("ascii"),
            "correlation_id": "cid-abc",
            "move_number": 3,
        }
    ).encode()
    with capture_logs() as cap:
        await handle_turn(body)
    assert cap == [
        {
            "event": "turn_handled",
            "correlation_id": "cid-abc",
            "move_number": 3,
            "outcome": "success",
            "log_level": "info",
        }
    ]


async def test_handle_turn_defaults_correlation_id_to_empty_string() -> None:
    """Payload without correlation_id must log correlation_id == ""."""
    body = json.dumps(
        {
            "symbol": "X",
            "board": ".|.|.\n.|.|.\n.|.|.",
            "source_b64": base64.b64encode(SIMPLE_BOT).decode("ascii"),
        }
    ).encode()
    with capture_logs() as cap:
        await handle_turn(body)
    assert cap[0]["correlation_id"] == ""


async def test_handle_turn_defaults_move_number_to_zero() -> None:
    """Payload without move_number must log move_number == 0."""
    body = json.dumps(
        {
            "symbol": "X",
            "board": ".|.|.\n.|.|.\n.|.|.",
            "source_b64": base64.b64encode(SIMPLE_BOT).decode("ascii"),
        }
    ).encode()
    with capture_logs() as cap:
        await handle_turn(body)
    assert cap[0]["move_number"] == 0


async def test_handle_turn_uses_initial_sentinel_defaults_on_early_exception() -> None:
    """When b64 decode fails before .get() assignments, initial "" and 0 are used."""
    body = json.dumps(
        {
            "symbol": "X",
            "board": ".|.|.\n.|.|.\n.|.|.",
            "source_b64": "!!!not-valid-base64!!!",
        }
    ).encode()
    with capture_logs() as cap:
        await handle_turn(body)
    assert cap[0]["correlation_id"] == ""
    assert cap[0]["move_number"] == 0


async def test_handle_turn_logs_error_outcome_on_bot_failure() -> None:
    body = json.dumps(
        {
            "symbol": "X",
            "board": ".|.|.\n.|.|.\n.|.|.",
            "source_b64": base64.b64encode(b"import sys\nsys.exit(1)\n").decode(
                "ascii"
            ),
            "correlation_id": "cid-def",
            "move_number": 2,
        }
    ).encode()
    with capture_logs() as cap:
        await handle_turn(body)
    assert cap[0]["event"] == "turn_handled"
    assert cap[0]["outcome"] == "error"
    assert cap[0]["correlation_id"] == "cid-def"
