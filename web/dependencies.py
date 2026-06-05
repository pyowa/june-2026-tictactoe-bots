"""FastAPI dependency providers.

`get_db_session` opens a per-request `AsyncSession` so routes don't need to
manage the context manager directly. The repository providers
(`get_bots`, `get_matches`, `get_moves`) construct a repo bound to that
session — each is a thin pass-through so tests can override individual
repos via `app.dependency_overrides[get_bots] = lambda: fake`."""

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

import db.session
from entities.bot.repository import BotRepository
from entities.match.repository import MatchRepository
from entities.move.repository import MoveRepository
from messaging.queue import Queue


async def get_db_session() -> AsyncIterator[AsyncSession]:
    # Look up `session_factory` on the module at call time so tests that call
    # `db.session.reconfigure(url)` (which rebinds the module attribute) take
    # effect for subsequent requests. A `from db.session import session_factory`
    # at the top would freeze the import-time binding.
    async with db.session.session_factory() as session:
        yield session


def get_bots(session: AsyncSession = Depends(get_db_session)) -> BotRepository:
    return BotRepository(session)


def get_matches(session: AsyncSession = Depends(get_db_session)) -> MatchRepository:
    return MatchRepository(session)


def get_moves(session: AsyncSession = Depends(get_db_session)) -> MoveRepository:
    return MoveRepository(session)


def get_queue(request: Request) -> Queue:
    """FastAPI dependency: return the process-wide queue created by the
    `lifespan` in `web/main.py` and stashed on `app.state`.

    Tests substitute a fake by setting
    `app.dependency_overrides[get_queue] = lambda: fake`."""
    return request.app.state.queue
