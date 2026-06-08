"""
Broker-agnostic connection abstraction.

All aio_pika-specific wiring stays inside messaging/. Callers in runner/
receive a `BrokerConnection` from `messaging.client.make_connection()` and
never import aio_pika directly.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from messaging.rpc_client import RpcCaller, RpcClient
from messaging.rpc_server import serve_rpc as _serve_rpc


class BrokerConnection(Protocol):
    async def make_rpc_client(self) -> RpcCaller: ...
    async def consume_queue(
        self,
        queue_name: str,
        handler: Callable[[bytes], Awaitable[None]],
    ) -> None: ...
    async def serve_rpc(
        self,
        queue_name: str,
        handler: Callable[[bytes], Awaitable[bytes]],
    ) -> None: ...
    async def close(self) -> None: ...


class RabbitMQBrokerConnection:
    """Wraps an aio_pika connection with broker-agnostic methods.

    Constructed by `messaging.client.make_connection()`; never instantiated
    directly outside messaging/."""

    def __init__(self, connection: Any, channel: Any) -> None:  # pragma: no cover
        self._connection = connection
        self._channel = channel

    async def make_rpc_client(self) -> RpcClient:  # pragma: no cover
        return await RpcClient.create(self._channel)

    async def consume_queue(  # pragma: no cover
        self,
        queue_name: str,
        handler: Callable[[bytes], Awaitable[None]],
    ) -> None:
        await self._channel.set_qos(prefetch_count=1)
        queue = await self._channel.declare_queue(queue_name, durable=True)

        async def _on_message(message: Any) -> None:
            async with message.process():
                await handler(message.body)

        await queue.consume(_on_message)

    async def serve_rpc(  # pragma: no cover
        self,
        queue_name: str,
        handler: Callable[[bytes], Awaitable[bytes]],
    ) -> None:
        await _serve_rpc(self._channel, queue_name, handler)

    async def close(self) -> None:  # pragma: no cover
        await self._connection.close()
