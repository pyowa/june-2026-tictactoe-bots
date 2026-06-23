import json
from typing import Any

import aio_pika

from messaging.contracts import (
    BUILD_POD_QUEUE,
    EVENTS_EXCHANGE,
    BuildPodMessage,
)


class RabbitMQQueue:
    """`Queue` implementation backed by RabbitMQ via `aio_pika`. The
    connection is opened lazily on first publish and reused thereafter
    (aio_pika's `connect_robust` reconnects in the background on failure)."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: Any = None
        self._channel: Any = None
        self._events_exchange: Any = None

    async def _ensure_connected(self) -> Any:
        if self._connection is None or self._connection.is_closed:  # pragma: no cover
            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel()
            await self._channel.declare_queue(BUILD_POD_QUEUE, durable=True)
            self._events_exchange = await self._channel.declare_exchange(
                EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=False
            )
        return self._channel

    async def enqueue_build_pod(self, msg: BuildPodMessage) -> None:
        channel = await self._ensure_connected()
        message = aio_pika.Message(
            body=msg.model_dump_json().encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await channel.default_exchange.publish(message, routing_key=BUILD_POD_QUEUE)

    async def publish_event(
        self, event_type: str, details: dict[str, Any] | None = None
    ) -> None:
        """Publish a dashboard event (e.g. bot.uploaded) to the fanout
        exchange the WebSocket endpoint forwards to subscribed dashboards."""
        await self._ensure_connected()
        body = json.dumps({"type": event_type, "details": details or {}}).encode()
        message = aio_pika.Message(body=body, content_type="application/json")
        await self._events_exchange.publish(message, routing_key="")

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
