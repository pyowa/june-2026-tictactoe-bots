"""
pod_builder: consumes matches.build from RabbitMQ, creates a long-lived k8s
pod per bot, waits for it to be HTTP-healthy, updates Postgres, then publishes
to matches.schedule so match_scheduler can proceed.
"""

import asyncio
import base64
import functools
import os
from typing import Any

import aio_pika
import structlog

from db.session import get_session
from dispatcher.pods import (
    build_bot_pod_manifest,
    get_pod_ip,
    pod_name,
    wait_for_http_ready,
    wait_for_pod_ready,
)
from entities.bot.repository import BotRepository
from messaging.amqp import parse_amqp_message
from messaging.contracts import (
    BUILD_POD_QUEUE,
    POD_READY_QUEUE,
    BuildPodMessage,
    PodReadyMessage,
)
from web.runtimes import RUNTIMES

RABBITMQ_URL = os.environ.get(
    "RABBITMQ_URL", "amqp://guest:guest@host.docker.internal:5672/"
)
POD_TIMEOUT = float(os.environ.get("POD_TIMEOUT", "60"))

_log = structlog.get_logger()


async def handle_build_pod_message(
    message: Any,
    channel: Any,
    core_v1: Any,
) -> None:
    async with message.process():
        msg = parse_amqp_message(message.body, BuildPodMessage)
        if msg is None:
            return

        runtime = RUNTIMES.get(msg.runtime_key)
        if runtime is None:
            _log.error("pod_builder_unknown_runtime", runtime_key=msg.runtime_key)
            return

        async with get_session() as session:
            bots = BotRepository(session)
            bot_map = await bots.by_ids([msg.bot_id])

        bot = bot_map.get(msg.bot_id)
        if bot is None:
            _log.error("pod_builder_bot_not_found", bot_id=msg.bot_id)
            return

        source_b64 = base64.b64encode(bot.source or b"").decode("ascii")
        _pod_name = pod_name(msg.bot_id)

        _log.info("pod_building", bot_id=msg.bot_id, pod_name=_pod_name)

        loop = asyncio.get_running_loop()

        def _build_and_wait() -> None:
            manifest = build_bot_pod_manifest(
                _pod_name, runtime.image, source_b64, msg.bot_id
            )
            core_v1.create_namespaced_pod("bots", body=manifest)
            wait_for_pod_ready(core_v1, _pod_name, timeout=POD_TIMEOUT)
            pod_ip = get_pod_ip(core_v1, _pod_name)
            wait_for_http_ready(pod_ip, timeout=POD_TIMEOUT)

        await loop.run_in_executor(None, functools.partial(_build_and_wait))

        async with get_session() as session:
            bots = BotRepository(session)
            await bots.set_pod_ready(msg.bot_id, _pod_name)

        await channel.default_exchange.publish(
            aio_pika.Message(
                body=PodReadyMessage(bot_id=msg.bot_id).model_dump_json().encode(),
            ),
            routing_key=POD_READY_QUEUE,
        )

        _log.info("pod_ready", bot_id=msg.bot_id, pod_name=_pod_name)


async def run() -> None:  # pragma: no cover
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
    queue = await channel.declare_queue(BUILD_POD_QUEUE, durable=True)

    async with queue.iterator() as it:
        async for message in it:
            await handle_build_pod_message(message, channel, core_v1)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
