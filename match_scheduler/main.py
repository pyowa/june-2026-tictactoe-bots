"""
match_scheduler: consumes matches.schedule from RabbitMQ, queries Postgres for
all ready bots, and publishes one MatchOndeck per pairing to matches.ondeck so
match_runner can proceed.
"""

import asyncio
import os
import secrets
from typing import Any

import aio_pika
import structlog

import entities.move.model  # noqa: F401 — registers Move with SQLAlchemy mapper
from db.session import get_session
from entities.bot.repository import BotRepository
from messaging.contracts import (
    MATCH_ONDECK_QUEUE,
    POD_READY_QUEUE,
    MatchOndeck,
    PodReadyMessage,
)

RABBITMQ_URL = os.environ.get(
    "RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
)

_log = structlog.get_logger()


async def handle_pod_ready_message(message: Any, channel: Any) -> None:
    async with message.process():
        try:
            msg = PodReadyMessage.model_validate_json(message.body)
        except Exception:
            _log.error("match_scheduler_invalid_json")
            return

        async with get_session() as session:
            repo = BotRepository(session)
            ready_bots = await repo.ready_bots()

        count = 0
        for other in ready_bots:
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=MatchOndeck(
                        bot_x_id=msg.bot_id,
                        bot_o_id=other.id,
                        correlation_id=secrets.token_hex(16),
                    )
                    .model_dump_json()
                    .encode(),
                ),
                routing_key=MATCH_ONDECK_QUEUE,
            )
            count += 1
            if other.id != msg.bot_id:
                await channel.default_exchange.publish(
                    aio_pika.Message(
                        body=MatchOndeck(
                            bot_x_id=other.id,
                            bot_o_id=msg.bot_id,
                            correlation_id=secrets.token_hex(16),
                        )
                        .model_dump_json()
                        .encode(),
                    ),
                    routing_key=MATCH_ONDECK_QUEUE,
                )
                count += 1

        _log.info("matches_scheduled", bot_id=msg.bot_id, count=count)


async def run() -> None:  # pragma: no cover
    from messaging.log import configure_logging

    configure_logging()

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(POD_READY_QUEUE, durable=True)

    async with queue.iterator() as it:
        async for message in it:
            await handle_pod_ready_message(message, channel)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
