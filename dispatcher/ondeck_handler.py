"""
ondeck_handler: consumes matches.ondeck from RabbitMQ, runs a match using
existing (permanent) bot pods, and writes the result directly to Postgres.
"""

import asyncio
import functools
import os
from typing import Any

import structlog

from db.session import get_session
from dispatcher.match_runner import run_match_from_pods
from entities.bot.repository import BotRepository
from entities.match.repository import MatchRepository
from messaging.contracts import (
    EVENT_MATCH_FINISHED,
    EVENTS_EXCHANGE,
    MATCH_ONDECK_QUEUE,
    MatchOndeck,
)

RABBITMQ_URL = os.environ.get(
    "RABBITMQ_URL", "amqp://guest:guest@host.docker.internal:5672/"
)

_log = structlog.get_logger()


async def _fetch_match_bots(bot_x_id: int, bot_o_id: int) -> tuple[Any, Any] | None:
    """Fetch both bots; validate they exist and have pods. Returns None on failure."""
    async with get_session() as session:
        bots = BotRepository(session)
        bot_map = await bots.by_ids([bot_x_id, bot_o_id])

    bot_x = bot_map.get(bot_x_id)
    bot_o = bot_map.get(bot_o_id)

    if bot_x is None or bot_o is None:
        _log.error("ondeck_handler_bot_not_found", bot_x_id=bot_x_id, bot_o_id=bot_o_id)
        return None

    if bot_x.pod_name is None or bot_o.pod_name is None:
        _log.error(
            "ondeck_handler_bot_has_no_pod", bot_x_id=bot_x_id, bot_o_id=bot_o_id
        )
        return None

    return bot_x, bot_o


async def handle_match_ondeck(
    message: Any,
    channel: Any,
    core_v1: Any,
) -> None:
    async with message.process():
        try:
            msg = MatchOndeck.model_validate_json(message.body)
        except Exception:
            _log.error("ondeck_handler_invalid_json")
            return

        bots_result = await _fetch_match_bots(msg.bot_x_id, msg.bot_o_id)
        if bots_result is None:
            return

        bot_x, bot_o = bots_result

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            functools.partial(
                run_match_from_pods,
                core_v1,
                bot_x.pod_name,
                bot_o.pod_name,
                msg.correlation_id,
            ),
        )

        async with get_session() as session:
            matches = MatchRepository(session)
            await matches.record(msg.bot_x_id, msg.bot_o_id, result, msg.correlation_id)

        # Publish to the events fanout so the dashboard WebSocket plays a
        # sound. Best-effort: if the broker is unhappy we log + swallow so
        # the consumer ack isn't blocked.
        try:
            await _publish_match_finished(channel, result.result)
        except Exception as exc:  # pragma: no cover -- broker outage path
            _log.warning("event_publish_failed", error=repr(exc))

        _log.info(
            "match_complete",
            correlation_id=msg.correlation_id,
            result=result.result,
        )


async def _publish_match_finished(channel: Any, result: str) -> None:
    """Publish a match.finished event to the dashboard fanout exchange."""
    import json

    import aio_pika

    exchange = await channel.declare_exchange(
        EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=False
    )
    body = json.dumps(
        {"type": EVENT_MATCH_FINISHED, "details": {"result": result}}
    ).encode()
    await exchange.publish(aio_pika.Message(body=body), routing_key="")


async def run() -> None:  # pragma: no cover
    import aio_pika
    from kubernetes import client, config

    from db.session import session_factory
    from messaging.health import make_health_echo_handler
    from messaging.log import configure_logging
    from messaging.rpc_server import serve_rpc

    configure_logging()
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()

    core_v1 = client.CoreV1Api()

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(MATCH_ONDECK_QUEUE, durable=True)

    echo_handler = make_health_echo_handler(
        session_factory, channel, MATCH_ONDECK_QUEUE
    )

    async def consume() -> None:
        async with queue.iterator() as it:
            async for message in it:
                await handle_match_ondeck(message, channel, core_v1)

    await asyncio.gather(
        consume(),
        serve_rpc(channel, "health.dispatcher.ondeck-handler", echo_handler),
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
