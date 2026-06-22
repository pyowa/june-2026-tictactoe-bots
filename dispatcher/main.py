"""Dispatcher entrypoint — runs pod_builder and ondeck_handler concurrently,
plus an HTTP `/health` gateway that RPCs into each consumer's echo handler."""

import asyncio

from dispatcher.ondeck_handler import run as run_ondeck
from dispatcher.pod_builder import run as run_pod_builder


async def run() -> None:  # pragma: no cover
    import uvicorn
    from fastapi import FastAPI

    from messaging.client import BROKER_URL
    from messaging.health import make_health_router, worker_echo_check

    health_app = FastAPI()
    health_app.include_router(
        make_health_router(
            {
                "pod_builder": worker_echo_check(
                    BROKER_URL, "health.dispatcher.pod-builder"
                ),
                "ondeck_handler": worker_echo_check(
                    BROKER_URL, "health.dispatcher.ondeck-handler"
                ),
            }
        )
    )
    health_server = uvicorn.Server(
        uvicorn.Config(health_app, host="0.0.0.0", port=8080, log_level="warning")
    )

    await asyncio.gather(run_pod_builder(), run_ondeck(), health_server.serve())


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
