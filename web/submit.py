"""POST /submit handler — extracted so `web/main.py` stays a thin route table.

The public surface is one async function, `handle_submission`. Internally
the flow is split into small steps; each validation helper returns a
user-facing error string on failure or `None` on success. Happy-path code
reads top-to-bottom with early returns on error."""

import re
import secrets
from dataclasses import dataclass

import structlog
from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse

from entities.bot.repository import BotRepository
from messaging.contracts import EVENT_BOT_UPLOADED, BuildPodMessage
from messaging.queue import Queue
from web.templates import render_submit_response
from web.utils import (
    _python_version_from_runtime_key,
    encode_cookie,
    extract_bot_name,
    extract_runtime_key,
    parse_cookie,
    versioned_name,
)

# Names ending in `V<digits>` are reserved — the auto-versioning system
# produces exactly that suffix for v2+, so accepting them as user input
# would create ambiguity (uploading "FooV9" alongside an auto-generated
# "FooV9" from "Foo" v9).
_log = structlog.get_logger()

_RESERVED_VERSION_SUFFIX = re.compile(r"V\d+$")


@dataclass
class _OwnerContext:
    owned: dict[str, str]
    owner_token: str
    bot_name: str


def _validate_bot_name(bot_name: str | None) -> str | None:
    """Return an error string if the bot name is missing or reserved, else None."""
    if not bot_name:
        return "Your bot must start with a docstring containing 'name: <name>'."
    if _RESERVED_VERSION_SUFFIX.search(bot_name):
        return (
            f"Bot name '{bot_name}' ends in 'V<digits>', which is "
            "reserved for auto-versioning. Pick a different name."
        )
    return None


def _validate_runtime_key(runtime_key: str | None) -> str | None:
    """Return an error string if the runtime key is missing, else None."""
    if runtime_key is None:
        return (
            "Invalid runtime in docstring. Use 'language: python-3.13' "
            "or 'python: 3.13'."
        )
    return None


async def _resolve_owner_token(
    bots: BotRepository, bot_name: str, owned: dict[str, str]
) -> tuple[str | None, str | None]:
    """Return (owner_token, error). On name collision returns (None, error_msg)."""
    existing_token = await bots.owner_token(bot_name)
    if existing_token:
        if owned.get(bot_name) != existing_token:
            return None, f"'{bot_name}' is already taken by someone else."
        return existing_token, None

    return secrets.token_hex(32), None  # pragma: no mutate -- token_hex(None) is equiv


async def _persist_bot(
    bots: BotRepository,
    bot_name: str,
    owner_token: str,
    runtime_key: str,
    source_bytes: bytes,
) -> tuple[str, int]:
    """Insert the new bot row and return `(versioned_name, bot_id)`."""
    python_version = _python_version_from_runtime_key(runtime_key)
    version = await bots.next_version(bot_name)
    name = versioned_name(bot_name, version)
    bot = await bots.create(
        base_name=bot_name,
        versioned_name=name,
        version=version,
        owner_token=owner_token,
        python_version=python_version,
        runtime_key=runtime_key,
        source=source_bytes,
    )
    return name, bot.id


def _success_response(
    request: Request,
    name: str,
    owner: _OwnerContext,
) -> HTMLResponse:
    """Render the submit page with a success banner and (re)set the ownership
    cookie so future submissions of the same base name are recognized."""
    owner.owned[owner.bot_name] = owner.owner_token
    response = render_submit_response(
        request, success=f"'{name}' submitted successfully!"
    )
    response.set_cookie(
        key="ttt_owned_bots",
        value=encode_cookie(owner.owned),
        httponly=True,
        samesite="lax",
    )
    return response


async def handle_submission(
    request: Request,
    file: UploadFile,
    owned_bots_cookie: str | None,
    queue: Queue,
    bots: BotRepository,
) -> HTMLResponse:
    """Top-level handler for POST /submit. Reads the uploaded source,
    validates it, persists the bot, enqueues match jobs, and renders either
    a success or error page."""
    source_bytes = await file.read()
    source = source_bytes.decode("utf-8", errors="replace")  # pragma: no mutate

    bot_name = extract_bot_name(source)
    if err := _validate_bot_name(bot_name):
        return render_submit_response(request, error=err)

    runtime_key = extract_runtime_key(source)
    if err := _validate_runtime_key(runtime_key):
        return render_submit_response(request, error=err)

    assert bot_name is not None  # narrowed above
    assert runtime_key is not None  # narrowed above

    python_version = _python_version_from_runtime_key(runtime_key)
    owned = parse_cookie(owned_bots_cookie)
    owner_token, err = await _resolve_owner_token(bots, bot_name, owned)
    if err:
        return render_submit_response(request, error=err)

    assert owner_token is not None  # narrowed above

    name, new_bot_id = await _persist_bot(
        bots, bot_name, owner_token, runtime_key, source_bytes
    )
    await queue.enqueue_build_pod(
        BuildPodMessage(bot_id=new_bot_id, runtime_key=runtime_key)
    )
    await queue.publish_event(EVENT_BOT_UPLOADED, {"versioned_name": name})
    _log.info(
        "bot_uploaded",
        bot_name=bot_name,
        bot_id=new_bot_id,
        python_version=python_version,
        runtime_key=runtime_key,
    )

    return _success_response(
        request,
        name,
        _OwnerContext(owned=owned, owner_token=owner_token, bot_name=bot_name),
    )
