"""
Process-wide access to the active message queue.

The active queue is a module-level singleton resolved lazily on first use, so
tests can swap in a fake via `set_queue()` without an env round-trip.
"""

import os

from messaging.queue import Queue

DEFAULT_BROKER_URL = "amqp://guest:guest@localhost:5672/"
BROKER_URL = os.environ.get("RABBITMQ_URL", DEFAULT_BROKER_URL)

_queue: Queue | None = None


def get_queue() -> Queue:
    global _queue
    if _queue is None:  # pragma: no cover
        # Real broker construction only fires outside tests; tests inject a
        # fake via set_queue().
        from messaging.rabbitmq import RabbitMQQueue

        _queue = RabbitMQQueue(BROKER_URL)
    return _queue


def set_queue(queue: Queue | None) -> None:
    """For tests: swap in a fake queue implementation, or pass `None` to
    reset so the next `get_queue()` call constructs a fresh real one."""
    global _queue
    _queue = queue
