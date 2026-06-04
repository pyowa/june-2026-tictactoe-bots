"""POST /submit handler — extracted so `web/main.py` stays a thin route table.

The public surface is one async function, `handle_submission`. Internally
the flow is split into small steps; failed validation in any step raises
`_SubmissionError`, which the top-level handler converts into a rendered
error response. Happy-path code therefore reads top-to-bottom with no
sentinel returns."""

import secrets

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import (
    get_next_version,
    get_owner_token,
    get_session,
    insert_bot,
    list_bots,
)
from db.models.bot import Bot
from messaging.queue import Queue
from web.templates import render_index_response, templates
from web.utils import (
    encode_cookie,
    enqueue_match_pairs,
    extract_bot_name,
    extract_python_version,
    implied_base,
    parse_cookie,
    versioned_name,
)


class _SubmissionError(Exception):
    """Raised inside the submission flow when a step should short-circuit to
    an error render. The `message` attribute is the user-facing string."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _validate_source(source_bytes: bytes) -> tuple[str, str]:
    """Decode + validate the uploaded source.

    Returns `(bot_name, python_version)`. Raises `_SubmissionError` if the
    docstring is missing the `name:` field or the declared `python:` is
    unsupported."""
    source = source_bytes.decode("utf-8", errors="replace")

    bot_name = extract_bot_name(source)
    if not bot_name:
        raise _SubmissionError(
            "Your bot must start with a docstring containing 'name: <name>'."
        )

    python_version = extract_python_version(source)
    if python_version is None:
        raise _SubmissionError(
            "Invalid 'python:' value in docstring. "
            "Use a version like '3', '3.11', or '3.12'."
        )

    return bot_name, python_version


async def _resolve_owner_token(
    session: AsyncSession, bot_name: str, owned: dict[str, str]
) -> str:
    """Return the owner token to use for this submission.

    Raises `_SubmissionError` if `bot_name` looks like a versioned name
    whose base already exists, or if the base is owned by somebody whose
    cookie doesn't match. Mints a fresh token when the name is unclaimed."""
    base = implied_base(bot_name)
    if base and await get_owner_token(session, base) is not None:
        raise _SubmissionError(
            f"'{bot_name}' looks like a versioned name. "
            f"Submit as '{base}' and versioning is handled automatically."
        )

    existing_token = await get_owner_token(session, bot_name)
    if existing_token:
        if owned.get(bot_name) != existing_token:
            raise _SubmissionError(
                f"'{bot_name}' is already taken by someone else."
            )
        return existing_token

    return secrets.token_hex(32)


async def _persist_bot(
    session: AsyncSession,
    bot_name: str,
    owner_token: str,
    python_version: str,
    source_bytes: bytes,
) -> tuple[str, int]:
    """Insert the new bot row and return `(versioned_name, bot_id)`."""
    version = await get_next_version(session, bot_name)
    name = versioned_name(bot_name, version)
    await insert_bot(
        session,
        bot_name,
        name,
        version,
        owner_token,
        python_version,
        source=source_bytes,
    )
    result = await session.execute(select(Bot.id).where(Bot.versioned_name == name))
    return name, result.scalar_one()


def _success_response(
    request: Request,
    name: str,
    owned: dict[str, str],
    owner_token: str,
    bot_name: str,
    bots: list,
) -> HTMLResponse:
    """Render the index page with a success banner and (re)set the ownership
    cookie so future submissions of the same base name are recognized."""
    owned[bot_name] = owner_token
    response = templates.TemplateResponse(
        request,
        "index.html",
        {"bots": bots, "success": f"'{name}' submitted successfully!"},
    )
    response.set_cookie(
        key="ttt_owned_bots",
        value=encode_cookie(owned),
        httponly=True,
        samesite="lax",
    )
    return response


async def handle_submission(
    request: Request,
    file: UploadFile,
    owned_bots_cookie: str | None,
    queue: Queue,
) -> HTMLResponse:
    """Top-level handler for POST /submit. Reads the uploaded source,
    validates it, persists the bot, enqueues match jobs, and renders either
    a success or error page."""
    source_bytes = await file.read()
    try:
        bot_name, python_version = _validate_source(source_bytes)
        owned = parse_cookie(owned_bots_cookie)
        async with get_session() as session:
            owner_token = await _resolve_owner_token(session, bot_name, owned)
            name, new_bot_id = await _persist_bot(
                session, bot_name, owner_token, python_version, source_bytes
            )
            await enqueue_match_pairs(queue, session, new_bot_id, python_version)
            bots = await list_bots(session)
    except _SubmissionError as exc:
        return render_index_response(request, error=exc.message)

    return _success_response(request, name, owned, owner_token, bot_name, bots)
