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
from messaging.amqp import parse_amqp_message
from messaging.contracts import MATCH_ONDECK_QUEUE, MatchOndeck

RABBITMQ_URL = os.environ.get(
    "RABBITMQ_URL", "amqp://guest:guest@host.docker.internal:5672/"
)

_log = structlog.get_logger()


async def handle_match_ondeck(
    message: Any,
    channel: Any,
    core_v1: Any,
) -> None:
    async with message.process():
        msg = parse_amqp_message(message.body, MatchOndeck)
        if msg is None:
            return

        async with get_session() as session:
            bots = BotRepository(session)
            bot_map = await bots.by_ids([msg.bot_x_id, msg.bot_o_id])

        bot_x = bot_map.get(msg.bot_x_id)
        bot_o = bot_map.get(msg.bot_o_id)

        if bot_x is None or bot_o is None:
            _log.error(
                "ondeck_handler_bot_not_found",
                bot_x_id=msg.bot_x_id,
                bot_o_id=msg.bot_o_id,
            )
            return

        if bot_x.pod_name is None or bot_o.pod_name is None:
            _log.error(
                "ondeck_handler_bot_has_no_pod",
                bot_x_id=msg.bot_x_id,
                bot_o_id=msg.bot_o_id,
            )
            return

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

        _log.info(
            "match_complete",
            correlation_id=msg.correlation_id,
            result=result.result,
        )


async def run() -> None:  # pragma: no cover
    import aio_pika
    from kubernetes import client, config

    from messaging.log import configure_logging

    configure_logging()
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()

    core_v1 = client.CoreV1Api()

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(MATCH_ONDECK_QUEUE, durable=True)

    async with queue.iterator() as it:
        async for message in it:
            await handle_match_ondeck(message, channel, core_v1)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
