"""
Request/response RPC over RabbitMQ.

Pattern: each `RpcClient` owns a private auto-delete reply queue. Calls publish
a request with `correlation_id` + `reply_to`, then await a `Future` that the
reply-queue consumer resolves when the matching reply lands. `serve_rpc()` is
the worker side — register a handler and it'll be invoked per request, with
the return value published to the message's `reply_to`.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Self

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage


class RpcClient:
    def __init__(
        self,
        channel: AbstractChannel,
        reply_queue_name: str,
    ) -> None:
        self._channel = channel
        self._reply_queue_name = reply_queue_name
        self._pending: dict[str, asyncio.Future[bytes]] = {}

    @classmethod
    async def create(cls, channel: AbstractChannel) -> Self:
        reply_queue = await channel.declare_queue(exclusive=True, auto_delete=True)
        client = cls(channel, reply_queue.name)
        await reply_queue.consume(client._on_reply, no_ack=True)
        return client

    async def _on_reply(self, message: AbstractIncomingMessage) -> None:
        cid = message.correlation_id
        if cid is not None and cid in self._pending:
            future = self._pending.pop(cid)
            if not future.done():
                future.set_result(message.body)

    async def call(
        self, target_queue: str, payload: bytes, timeout: float = 10.0
    ) -> bytes:
        correlation_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        self._pending[correlation_id] = future

        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=payload,
                correlation_id=correlation_id,
                reply_to=self._reply_queue_name,
                expiration=int(timeout * 1000),
            ),
            routing_key=target_queue,
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending.pop(correlation_id, None)
            raise


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
