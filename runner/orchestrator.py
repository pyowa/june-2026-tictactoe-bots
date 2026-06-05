"""
Orchestrator: consumes `matches.todo`, drives matches via
`dispatch.handle_match_message`, replies to clients.
"""

import asyncio
import signal

import aio_pika

from messaging.client import BROKER_URL
from messaging.queue import MATCHES_QUEUE
from messaging.rpc_client import RpcClient
from runner.dispatch import handle_match_message


async def run() -> None:  # pragma: no cover
    """Connect to the broker and serve forever. Exercised by the smoke test;
    excluded from coverage because it's all wiring."""
    connection = await aio_pika.connect_robust(BROKER_URL)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)

    rpc = await RpcClient.create(channel)
    queue = await channel.declare_queue(MATCHES_QUEUE, durable=True)

    async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process():
            print(f"[orchestrator] received {message.body!r}")
            try:
                result = await handle_match_message(rpc, message.body)
                print(f"[orchestrator]   result: {result.result}")
            except Exception as exc:
                print(f"[orchestrator]   error: {exc}")

    await queue.consume(on_message)
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
        await connection.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
