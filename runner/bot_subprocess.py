"""
Run a bot's source as a subprocess; handle the RPC payload marshalling
around it.
"""

import base64
import json
import os
import subprocess
import tempfile

TURN_TIMEOUT = int(os.environ.get("TURN_TIMEOUT", "5"))


def run_bot_subprocess(
    source: bytes, symbol: str, board: str, timeout: int = TURN_TIMEOUT
) -> dict[str, str | None]:
    """Run the bot source as a subprocess and return its move (or an error)."""
    with tempfile.NamedTemporaryFile(
        suffix=".py", delete=False, mode="wb"
    ) as f:
        f.write(source)
        tmpfile_path = f.name

    try:
        proc = subprocess.run(
            ["python", tmpfile_path],
            input=f"{symbol}\n{board}",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"board": None, "error": f"timeout after {timeout}s"}
    except Exception as exc:
        return {"board": None, "error": f"runtime error: {exc}"}
    finally:
        os.unlink(tmpfile_path)

    stdout = proc.stdout.strip()
    if not stdout:
        stderr = proc.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        return {"board": None, "error": f"invalid output: empty response{detail}"}
    return {"board": stdout, "error": None}


async def handle_turn(body: bytes) -> bytes:
    """RPC handler: decode the turn request, run the bot, encode the reply."""
    try:
        payload = json.loads(body)
        source = base64.b64decode(payload["source_b64"])
        response = run_bot_subprocess(
            source=source,
            symbol=payload["symbol"],
            board=payload["board"],
        )
    except Exception as exc:
        response = {"board": None, "error": f"worker error: {exc}"}
    return json.dumps(response).encode()
