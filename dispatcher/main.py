"""Dispatcher entrypoint — runs pod_builder and ondeck_handler concurrently."""
import asyncio

from dispatcher.ondeck_handler import run as run_ondeck
from dispatcher.pod_builder import run as run_pod_builder


async def run() -> None:  # pragma: no cover
    await asyncio.gather(run_pod_builder(), run_ondeck())


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
