import os

import aio_pika

from messaging.connection import BrokerConnection, RabbitMQBrokerConnection
from messaging.rabbitmq import RabbitMQQueue

DEFAULT_BROKER_URL = "amqp://guest:guest@localhost:5672/"
BROKER_URL = os.environ.get("RABBITMQ_URL", DEFAULT_BROKER_URL)


def make_queue() -> RabbitMQQueue:
    """Construct a queue bound to the configured broker. Returns the concrete
    `RabbitMQQueue` (not the `Queue` protocol) so the caller owning lifecycle
    can `close()` it at shutdown. Construction is cheap — the AMQP connection
    opens lazily on first publish."""
    return RabbitMQQueue(BROKER_URL)


async def make_connection() -> BrokerConnection:  # pragma: no cover
    """Open a RabbitMQ connection and return a broker-agnostic wrapper.
    Called once per process at startup; the connection is long-lived."""
    connection = await aio_pika.connect_robust(BROKER_URL)  # pragma: no mutate
    channel = await connection.channel()  # pragma: no mutate
    return RabbitMQBrokerConnection(connection, channel)  # pragma: no mutate
