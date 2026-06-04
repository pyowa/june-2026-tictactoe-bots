"""
Turn worker: consumes `turn.pyX.Y.requests`, writes the bot source to a
tmpfile, runs `python <tmpfile>` as a subprocess, and replies to the
orchestrator with the resulting board (or an error).

`WORKER_PYTHON_VERSION` (env) selects which queue this worker listens on.
"""

import asyncio
import base64
import json
import os
import subprocess
import tempfile

import aio_pika

from messaging import BROKER_URL
from messaging.routing import turn_queue_for
from messaging.rpc import serve_rpc

WORKER_PYTHON_VERSION = os.environ.get("WORKER_PYTHON_VERSION", "3")
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


async def run() -> None:  # pragma: no cover
    queue_name = turn_queue_for(WORKER_PYTHON_VERSION)
    print(f"Worker py{WORKER_PYTHON_VERSION} listening on {queue_name}")
    connection = await aio_pika.connect_robust(BROKER_URL)
    channel = await connection.channel()
    try:
        await serve_rpc(channel, queue_name, handle_turn)
    finally:
        await connection.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
