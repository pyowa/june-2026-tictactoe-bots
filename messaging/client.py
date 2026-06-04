import os

from messaging.rabbitmq import RabbitMQQueue

DEFAULT_BROKER_URL = "amqp://guest:guest@localhost:5672/"
BROKER_URL = os.environ.get("RABBITMQ_URL", DEFAULT_BROKER_URL)


def make_queue() -> RabbitMQQueue:
    """Construct a queue bound to the configured broker. Returns the concrete
    `RabbitMQQueue` (not the `Queue` protocol) so the caller owning lifecycle
    can `close()` it at shutdown. Construction is cheap — the AMQP connection
    opens lazily on first publish."""
    return RabbitMQQueue(BROKER_URL)
