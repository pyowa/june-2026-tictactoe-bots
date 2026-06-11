"""
Orchestrator: consumes `matches.todo`, drives matches via
`dispatch.handle_match_message`, replies to clients.
"""

import asyncio
import signal

from messaging.client import make_connection
from messaging.log import configure_logging
from messaging.queue import MATCHES_QUEUE
from runner.dispatch import handle_match_message


async def run() -> None:  # pragma: no cover
    """Connect to the broker and serve forever. Exercised by the smoke test;
    excluded from coverage because it's all wiring."""
    configure_logging()
    conn = await make_connection()
    rpc = await conn.make_rpc_client()

    async def on_message(body: bytes) -> None:
        print(f"[orchestrator] received {body!r}")
        try:
            result = await handle_match_message(rpc, body)
            print(f"[orchestrator]   result: {result.result}")
        except Exception as exc:
            print(f"[orchestrator]   error: {exc}")

    await conn.consume_queue(MATCHES_QUEUE, on_message)
    print("Orchestrator running. Ctrl+C to stop.")

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: stop.set_result(None) if not stop.done() else None,
        )
    try:
        await stop
    finally:
        await conn.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
