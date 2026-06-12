from typing import Protocol

from messaging.contracts import BuildPodMessage


class Queue(Protocol):
    async def enqueue_build_pod(self, msg: BuildPodMessage) -> None: ...
