"""
match_scheduler: consumes matches.schedule from RabbitMQ, queries Postgres for
all ready bots, and publishes one MatchOndeck per pairing to matches.ondeck so
match_runner can proceed.
"""

import asyncio
import secrets
from typing import Any

import aio_pika
import structlog

import entities.move.model  # noqa: F401 — registers Move with SQLAlchemy mapper
from db.session import get_session
from entities.bot.repository import BotRepository
from messaging.client import BROKER_URL
from messaging.contracts import (
    MATCH_ONDECK_QUEUE,
    POD_READY_QUEUE,
    MatchOndeck,
    PodReadyMessage,
)

_log = structlog.get_logger()


async def _enqueue_matches(channel: Any, bot_id: int, ready_bots: list[Any]) -> int:
    """Publish MatchOndeck messages for all pairings involving bot_id.

    Returns the number of messages published."""
    count = 0
    for other in ready_bots:
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=MatchOndeck(
                    bot_x_id=bot_id,
                    bot_o_id=other.id,
                    correlation_id=secrets.token_hex(16),
                )
                .model_dump_json()
                .encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=MATCH_ONDECK_QUEUE,
        )
        count += 1
        if other.id != bot_id:
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=MatchOndeck(
                        bot_x_id=other.id,
                        bot_o_id=bot_id,
                        correlation_id=secrets.token_hex(16),
                    )
                    .model_dump_json()
                    .encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key=MATCH_ONDECK_QUEUE,
            )
            count += 1
    return count


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

        count = await _enqueue_matches(channel, msg.bot_id, ready_bots)
        _log.info("matches_scheduled", bot_id=msg.bot_id, count=count)


async def run() -> None:  # pragma: no cover
    import uvicorn
    from fastapi import FastAPI

    from db.session import session_factory
    from messaging.health import (
        make_health_echo_handler,
        make_health_router,
        worker_echo_check,
    )
    from messaging.log import configure_logging
    from messaging.rpc_server import serve_rpc

    configure_logging()

    connection = await aio_pika.connect_robust(BROKER_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(POD_READY_QUEUE, durable=True)

    health_rpc_queue = "health.match-scheduler"
    echo_handler = make_health_echo_handler(
        session_factory, channel, MATCH_ONDECK_QUEUE
    )

    health_app = FastAPI()
    health_app.include_router(
        make_health_router(
            {"match_scheduler": worker_echo_check(BROKER_URL, health_rpc_queue)}
        )
    )
    health_server = uvicorn.Server(
        uvicorn.Config(health_app, host="0.0.0.0", port=8080, log_level="warning")
    )

    async def consume() -> None:
        async with queue.iterator() as it:
            async for message in it:
                await handle_pod_ready_message(message, channel)

    await asyncio.gather(
        consume(),
        serve_rpc(channel, health_rpc_queue, echo_handler),
        health_server.serve(),
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
