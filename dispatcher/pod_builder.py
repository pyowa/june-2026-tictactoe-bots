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
    wait_for_http_ready,
    wait_for_pod_ready,
)
from dispatcher.pods import (
    pod_name as make_pod_name,
)
from entities.bot.model import Bot
from entities.bot.repository import BotRepository
from messaging.amqp import parse_amqp_message
from messaging.client import BROKER_URL
from messaging.contracts import (
    BUILD_POD_QUEUE,
    POD_READY_QUEUE,
    BuildPodMessage,
    PodReadyMessage,
)
from web.runtimes import RUNTIMES, Runtime

POD_TIMEOUT = float(os.environ.get("POD_TIMEOUT", "60"))

_log = structlog.get_logger()


async def _fetch_bot_and_runtime(
    msg: BuildPodMessage,
) -> tuple[Bot, Runtime] | None:
    """DB lookup + runtime validation + logging.

    Returns None if bot not found or runtime unknown (caller returns early)."""
    runtime = RUNTIMES.get(msg.runtime_key)
    if runtime is None:
        _log.error("pod_builder_unknown_runtime", runtime_key=msg.runtime_key)
        return None

    async with get_session() as session:
        bots = BotRepository(session)
        bot_map = await bots.by_ids([msg.bot_id])

    bot = bot_map.get(msg.bot_id)
    if bot is None:
        _log.error("pod_builder_bot_not_found", bot_id=msg.bot_id)
        return None

    return bot, runtime


async def _build_register_and_notify(
    bot: Bot,
    runtime: Runtime,
    core_v1: Any,
    channel: Any,
) -> None:
    """Pod build + set_pod_ready DB update + publish PodReadyMessage."""
    source_b64 = base64.b64encode(bot.source or b"").decode("ascii")
    pname = make_pod_name(bot.id)

    _log.info("pod_building", bot_id=bot.id, pod_name=pname)

    loop = asyncio.get_running_loop()

    def _build_and_wait() -> None:
        manifest = build_bot_pod_manifest(pname, runtime.image, source_b64, bot.id)
        core_v1.create_namespaced_pod("bots", body=manifest)
        wait_for_pod_ready(core_v1, pname, timeout=POD_TIMEOUT)
        pod_ip = get_pod_ip(core_v1, pname)
        wait_for_http_ready(pod_ip, timeout=POD_TIMEOUT)

    await loop.run_in_executor(None, functools.partial(_build_and_wait))

    async with get_session() as session:
        bots = BotRepository(session)
        await bots.set_pod_ready(bot.id, pname)

    await channel.default_exchange.publish(
        aio_pika.Message(
            body=PodReadyMessage(bot_id=bot.id).model_dump_json().encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key=POD_READY_QUEUE,
    )

    _log.info("pod_ready", bot_id=bot.id, pod_name=pname)


async def handle_build_pod_message(
    message: Any,
    channel: Any,
    core_v1: Any,
) -> None:
    async with message.process():
        msg = parse_amqp_message(message.body, BuildPodMessage)
        if msg is None:
            return

        result = await _fetch_bot_and_runtime(msg)
        if result is None:
            return
        bot, runtime = result

        await _build_register_and_notify(bot, runtime, core_v1, channel)


async def run() -> None:  # pragma: no cover
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

    connection = await aio_pika.connect_robust(BROKER_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(BUILD_POD_QUEUE, durable=True)

    echo_handler = make_health_echo_handler(session_factory, channel, POD_READY_QUEUE)

    async def consume() -> None:
        async with queue.iterator() as it:
            async for message in it:
                await handle_build_pod_message(message, channel, core_v1)

    await asyncio.gather(
        consume(),
        serve_rpc(channel, "health.dispatcher.pod-builder", echo_handler),
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
