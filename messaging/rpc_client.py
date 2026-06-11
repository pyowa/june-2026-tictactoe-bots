"""
Caller side of RPC over RabbitMQ. Owns a private auto-delete reply queue;
publishes requests with `correlation_id` + `reply_to`; resolves a `Future`
per pending call when the matching reply lands.
"""

import asyncio
import uuid
from typing import Protocol, Self

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage


class RpcCaller(Protocol):
    """Broker-agnostic interface for making RPC calls. Satisfied by `RpcClient`
    and by test fakes (`_ScriptedRpc`, `_TimeoutRpc`, etc.)."""

    async def call(
        self, target_queue: str, payload: bytes, timeout: float = ...
    ) -> bytes: ...


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
        if cid in self._pending:
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
            self._pending.pop(correlation_id, None)  # pragma: no mutate -- race guard
            raise
