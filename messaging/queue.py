from dataclasses import dataclass
from typing import Protocol

MATCHES_QUEUE = "matches.todo"


@dataclass(frozen=True)
class MatchJob:
    bot_x_id: int
    bot_o_id: int
    python_version: str


class Queue(Protocol):
    async def enqueue_match(self, job: MatchJob) -> None: ...
