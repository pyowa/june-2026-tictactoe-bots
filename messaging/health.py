"""Health-probe primitives shared by every service that exposes `/health`.

Each check is a factory that returns an async callable yielding
`(status, reason)` — `("ok", None)` on success, `("down", "<repr>")` on
failure. `make_health_router` composes any set of checks into a FastAPI
`APIRouter` that returns 200 when all checks pass and 503 otherwise.

Probes open and close their own broker connection rather than reusing the
service's worker connection — that way a stuck consumer can't silently mask
broker outages, and a misbehaving health probe can't poison work traffic.
"""

import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from messaging.rpc_client import RpcClient

HealthCheck = Callable[[], Awaitable[tuple[str, str | None]]]


def db_check(session_factory: async_sessionmaker) -> HealthCheck:
    """Probe: open a session, run `SELECT 1`, close. Verifies the engine can
    reach Postgres and that auth + DB-name are valid."""

    async def _check() -> tuple[str, str | None]:
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            return "ok", None
        except Exception as e:
            return "down", repr(e)

    return _check


def broker_check(broker_url: str, queue_name: str) -> HealthCheck:
    """Probe: connect, passive-declare `queue_name`, close. Verifies the
    broker is reachable AND that the queue exists (which it will, since
    the consuming service declares it as part of its bootstrap)."""

    async def _check() -> tuple[str, str | None]:
        connection = None
        try:
            connection = await aio_pika.connect(broker_url, timeout=2)
            channel = await connection.channel()
            await channel.declare_queue(queue_name, passive=True)
            return "ok", None
        except Exception as e:
            return "down", repr(e)
        finally:
            if connection is not None:
                await connection.close()

    return _check


def make_health_echo_handler(
    session_factory: async_sessionmaker,
    channel: Any,
    publish_queue_name: str,
) -> Callable[[bytes], Awaitable[bytes]]:
    """RPC handler for a worker's `health.<consumer>` queue.

    Runs the worker's own DB SELECT 1 + a passive declare on its outbound
    publish queue (reusing the consumer's existing channel — no new broker
    connection), then echoes the request payload back inside a JSON
    envelope. The /health gateway parses the reply and rolls each sub-status
    into the overall response."""

    async def _handler(payload: bytes) -> bytes:
        db_status, db_reason = await db_check(session_factory)()
        try:
            await channel.declare_queue(publish_queue_name, passive=True)
            publish_queue_field = "ok"
        except Exception as e:
            publish_queue_field = f"down: {e!r}"
        db_field = "ok" if db_status == "ok" else f"down: {db_reason}"
        return json.dumps(
            {
                "echo": payload.decode(),
                "db": db_field,
                "publish_queue": publish_queue_field,
            }
        ).encode()

    return _handler


def worker_echo_check(
    broker_url: str, rpc_queue: str, timeout: float = 2.0
) -> HealthCheck:
    """Probe (used by a worker's /health gateway): open a fresh broker
    connection + RpcClient, RPC a random nonce into `rpc_queue`, wait up to
    `timeout` for the worker's echo handler to reply.

    Returns `("ok", None)` only if the reply echoes the nonce *and* every
    sub-status in the reply is `"ok"`. Otherwise returns `("down", reason)`
    where reason names the failing sub-status (or `"echo mismatch"` /
    connection error / timeout)."""

    async def _check() -> tuple[str, str | None]:
        nonce = secrets.token_hex(8)
        connection = None
        try:
            connection = await aio_pika.connect(broker_url, timeout=timeout)
            channel = await connection.channel()
            client = await RpcClient.create(channel)
            reply_bytes = await client.call(rpc_queue, nonce.encode(), timeout=timeout)
            reply = json.loads(reply_bytes)
            if reply.get("echo") != nonce:
                return "down", f"echo mismatch (got {reply.get('echo')!r})"
            for field, value in reply.items():
                if field == "echo":
                    continue
                if value != "ok":
                    return "down", f"{field}: {value}"
            return "ok", None
        except Exception as e:
            return "down", repr(e)
        finally:
            if connection is not None:
                await connection.close()

    return _check


def make_health_router(checks: dict[str, HealthCheck]) -> APIRouter:
    """Return an `APIRouter` exposing `GET /health`. Runs all checks
    sequentially; responds 200 with `{name: "ok", ...}` when every check
    passes, 503 with `{name: "ok" | "down: <reason>", ...}` otherwise."""

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        results: dict[str, str] = {}
        for name, check in checks.items():
            status, reason = await check()
            results[name] = "ok" if status == "ok" else f"down: {reason}"
        if any(v != "ok" for v in results.values()):
            raise HTTPException(status_code=503, detail=results)
        return results

    return router
