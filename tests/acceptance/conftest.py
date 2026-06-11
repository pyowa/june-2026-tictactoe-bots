import os
from collections.abc import AsyncIterator

import aio_pika
import pytest_asyncio
from aio_pika.abc import AbstractChannel, AbstractQueue

BROKER_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")


@pytest_asyncio.fixture
async def broker() -> AsyncIterator[AbstractChannel]:
    connection = await aio_pika.connect_robust(BROKER_URL)
    try:
        channel = await connection.channel()
        yield channel
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def reply_queue(broker: AbstractChannel) -> AsyncIterator[AbstractQueue]:
    queue = await broker.declare_queue(exclusive=True, auto_delete=True)
    yield queue
