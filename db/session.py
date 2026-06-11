"""Engine, async session factory, and the `get_session()` context manager
used by non-FastAPI callers (orchestrator, scripts, tests).

The FastAPI DI dependency wrapper around `session_factory` lives in
`web/dependencies.py` as `get_db_session` — that's the only consumer that
should `Depends()` on it; everything else uses `async with get_session()`."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

DEFAULT_ASYNC_URL = "postgresql+asyncpg://ttt:ttt@localhost:5432/ttt"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_ASYNC_URL)

_engine = create_async_engine(DATABASE_URL)
session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def reconfigure(url: str) -> None:
    """Rebind the async engine to a new URL (used by tests).

    Uses NullPool so each request gets a fresh connection — avoids asyncpg's
    'attached to a different loop' errors when tests spin up multiple
    TestClient instances (each with its own event loop)."""
    global _engine, session_factory, DATABASE_URL
    DATABASE_URL = url
    _engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,  # pragma: no mutate -- None is falsy here
    )


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Open an `AsyncSession` and close it on exit. Used by callers that
    aren't a FastAPI route (orchestrator, scripts, conftest helpers)."""
    async with session_factory() as session:
        yield session
