"""
Turn worker: consumes `turn.pyX.Y.requests` and dispatches each request to
`bot_subprocess.handle_turn`. `WORKER_PYTHON_VERSION` (env) selects which
queue this worker listens on.
"""

import asyncio
import os

from messaging.client import make_connection
from messaging.routing import turn_queue_for
from runner.bot_subprocess import handle_turn

WORKER_PYTHON_VERSION = os.environ.get("WORKER_PYTHON_VERSION", "3")


async def run() -> None:  # pragma: no cover
    queue_name = turn_queue_for(WORKER_PYTHON_VERSION)
    print(f"Worker py{WORKER_PYTHON_VERSION} listening on {queue_name}")
    conn = await make_connection()
    try:
        await conn.serve_rpc(queue_name, handle_turn)
    finally:
        await conn.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
