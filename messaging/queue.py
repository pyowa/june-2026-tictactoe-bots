from typing import Any, Protocol

from messaging.contracts import BuildPodMessage


class Queue(Protocol):
    async def enqueue_build_pod(self, msg: BuildPodMessage) -> None: ...
    async def publish_event(
        self, event_type: str, details: dict[str, Any] | None = None
    ) -> None: ...
