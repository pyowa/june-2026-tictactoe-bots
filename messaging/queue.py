from typing import Protocol

from pydantic import BaseModel, ConfigDict

MATCHES_QUEUE = "matches.todo"


class MatchJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_x_id: int
    bot_o_id: int
    python_version: str
    correlation_id: str


class Queue(Protocol):
    async def enqueue_match(self, job: MatchJob) -> None: ...
