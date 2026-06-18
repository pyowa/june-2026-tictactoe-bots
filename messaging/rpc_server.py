"""
Server side of RPC over RabbitMQ. `serve_rpc` consumes a request queue,
invokes a handler per message, and publishes the handler's return value to
`message.reply_to` (the caller's reply queue).
"""

import asyncio
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage


# TODO smell
async def serve_rpc(  # pragma: no cover
    channel: AbstractChannel,
    queue_name: str,
    handler: Callable[[bytes], Awaitable[bytes]],
) -> None:
    """Consume `queue_name`, call `handler(body)` for each message, publish
    its return value to `message.reply_to` (if set). Blocks forever.

    Wiring only; the handler itself is unit-tested separately (see
    `tests/test_turn_worker.py`)."""
    await channel.set_qos(prefetch_count=1)
    queue = await channel.declare_queue(queue_name, durable=True)

    async def on_message(message: AbstractIncomingMessage) -> None:
        async with message.process():
            response_body = await handler(message.body)
            if message.reply_to:
                await channel.default_exchange.publish(
                    aio_pika.Message(
                        body=response_body,
                        correlation_id=message.correlation_id,
                    ),
                    routing_key=message.reply_to,
                )

    await queue.consume(on_message)
    await asyncio.Future()  # block forever
