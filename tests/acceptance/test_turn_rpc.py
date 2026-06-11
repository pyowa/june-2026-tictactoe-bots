"""
Acceptance test: publish on the stable `turn.requests` contract, get a
valid reply back. Survives the docker→k8s implementation swap unchanged.
"""

import asyncio
import base64
import os
import uuid

import aio_pika
import pytest
from aio_pika.abc import AbstractChannel, AbstractQueue

from messaging.contracts import TURN_REQUEST_QUEUE, TurnReply, TurnRequest

pytestmark = pytest.mark.acceptance

ACCEPTANCE_TIMEOUT = float(os.environ.get("ACCEPTANCE_TIMEOUT", "10"))


FIRST_EMPTY_CELL_BOT = b'''"""
name: First Empty Cell
"""
import sys

data = sys.stdin.read().strip().splitlines()
symbol = data[0]
board = [r.split("|") for r in data[1:]]

for r in range(3):
    for c in range(3):
        if board[r][c] == ".":
            board[r][c] = symbol
            print("\\n".join("|".join(row) for row in board))
            sys.exit(0)
'''


async def test_turn_rpc_roundtrip(
    broker: AbstractChannel, reply_queue: AbstractQueue
) -> None:
    correlation_id = uuid.uuid4().hex
    request = TurnRequest(
        symbol="X",
        board=".|.|.\n.|.|.\n.|.|.",
        source_b64=base64.b64encode(FIRST_EMPTY_CELL_BOT).decode("ascii"),
        runtime_key="python-3.13",
    )

    await broker.declare_queue(TURN_REQUEST_QUEUE, durable=True)
    await broker.default_exchange.publish(
        aio_pika.Message(
            body=request.model_dump_json().encode(),
            correlation_id=correlation_id,
            reply_to=reply_queue.name,
        ),
        routing_key=TURN_REQUEST_QUEUE,
    )

    try:
        async with reply_queue.iterator(timeout=ACCEPTANCE_TIMEOUT) as it:
            async for message in it:
                if message.correlation_id != correlation_id:
                    await message.ack()
                    continue
                async with message.process():
                    reply = TurnReply.model_validate_json(message.body)
                    assert reply.error is None, (
                        f"bot reported error: {reply.error}"
                    )
                    assert reply.board == "X|.|.\n.|.|.\n.|.|."
                    return
    except asyncio.TimeoutError:
        pytest.fail(
            f"No reply received on {reply_queue.name} within "
            f"{ACCEPTANCE_TIMEOUT}s — is anything consuming {TURN_REQUEST_QUEUE}?"
        )
