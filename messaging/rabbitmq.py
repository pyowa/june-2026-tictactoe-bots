from typing import Any

import aio_pika

from messaging.contracts import BUILD_POD_QUEUE, BuildPodMessage
from messaging.queue import MATCHES_QUEUE, MatchJob


class RabbitMQQueue:
    """`Queue` implementation backed by RabbitMQ via `aio_pika`. The
    connection is opened lazily on first publish and reused thereafter
    (aio_pika's `connect_robust` reconnects in the background on failure)."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: Any = None
        self._channel: Any = None

    async def _ensure_connected(self) -> Any:
        if self._connection is None or self._connection.is_closed:  # pragma: no cover
            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel()
            await self._channel.declare_queue(MATCHES_QUEUE, durable=True)
            await self._channel.declare_queue(BUILD_POD_QUEUE, durable=True)
        return self._channel

    async def enqueue_match(self, job: MatchJob) -> None:
        channel = await self._ensure_connected()
        message = aio_pika.Message(
            body=job.model_dump_json().encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await channel.default_exchange.publish(message, routing_key=MATCHES_QUEUE)

    async def enqueue_build_pod(self, msg: BuildPodMessage) -> None:
        channel = await self._ensure_connected()
        message = aio_pika.Message(
            body=msg.model_dump_json().encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await channel.default_exchange.publish(message, routing_key=BUILD_POD_QUEUE)

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
