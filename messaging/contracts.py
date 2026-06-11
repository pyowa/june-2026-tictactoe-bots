"""
Stable contract for the per-turn RPC.

Both the current docker worker fleet (`runner/turn_worker.py`) and the
future k8s dispatcher will honor this shape, so the acceptance tests in
`tests/acceptance/` survive the implementation swap unchanged.

The AMQP message properties carry `correlation_id` (matches reply to
request) and `reply_to` (caller's exclusive reply queue). The JSON
payload does not duplicate those.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

TURN_REQUEST_QUEUE = "turn.requests"


class TurnRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: Literal["X", "O"]
    board: str  # 3 rows, "|"-delimited cells, "\n"-separated; "." = empty
    source_b64: str
    runtime_key: str  # key into the RUNTIMES allowlist, e.g. "python-3.13"


class TurnReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Exactly one of `board` / `error` is non-None per reply.
    board: str | None = None
    error: str | None = None
