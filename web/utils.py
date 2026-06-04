"""Pure helpers used by the web layer.

Anything in this module should be free of FastAPI / template / response I/O
so it can be unit-tested in isolation. The async helper here touches the DB
session and the message queue, but only via the objects passed in by the
caller (no global FastAPI state).
"""

import ast
import json
import re
import urllib.parse
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Bot
from messaging import MatchJob, get_queue, pick_python_version

# Python versions we accept on upload. The fleet of turn workers is sized
# to this set — adding a new version means adding a worker for it. When the
# `python:` field is omitted from a bot's docstring, the latest version
# in this tuple is used.
SUPPORTED_PYTHON_VERSIONS: tuple[str, ...] = (
    "3.10",
    "3.11",
    "3.12",
    "3.13",
    "3.14",
)
DEFAULT_PYTHON_VERSION = SUPPORTED_PYTHON_VERSIONS[-1]

_VERSIONED_RE = re.compile(r"^(.+)V\d+$")


def extract_bot_name(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    if not tree.body:
        return None

    first = tree.body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
        return None

    docstring = first.value.value
    if not isinstance(docstring, str):
        return None

    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("name:"):
            name = stripped[5:].strip()
            return name if name else None

    return None


def extract_python_version(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    if not tree.body:
        return None

    first = tree.body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
        return None

    docstring = first.value.value
    if not isinstance(docstring, str):
        return None

    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("python:"):
            version = stripped[7:].strip()
            if version in SUPPORTED_PYTHON_VERSIONS:
                return version
            return None  # present but not supported

    return DEFAULT_PYTHON_VERSION


def implied_base(name: str) -> str | None:
    """If `name` looks like FooV2, return Foo. Otherwise return None."""
    m = _VERSIONED_RE.match(name)
    return m.group(1) if m else None


def versioned_name(base_name: str, version: int) -> str:
    return base_name if version == 1 else f"{base_name}V{version}"


def parse_cookie(value: str | None) -> dict:
    if not value:
        return {}
    try:
        return json.loads(urllib.parse.unquote(value))
    except (json.JSONDecodeError, ValueError):
        return {}


def encode_cookie(owned: dict) -> str:
    return urllib.parse.quote(json.dumps(owned), safe="")


def group_matches_by_version(
    versions: list[Any], matches: list[Any]
) -> dict[str, list[Any]]:
    """Group `matches` by which versioned bot in `versions` participated.

    `versions` and `matches` are both duck-typed SQLAlchemy `Row` objects
    (`.versioned_name`, `.bot_x`, `.bot_o`), not ORM instances.

    A match where both sides are in the family (different versions) shows up
    under both; a true self-match (same versioned_name on both sides) shows
    up once."""
    versioned_names = {v.versioned_name for v in versions}
    grouped: dict[str, list[Any]] = {v.versioned_name: [] for v in versions}
    for m in matches:
        if m.bot_x in versioned_names:
            grouped[m.bot_x].append(m)
        if m.bot_o in versioned_names and m.bot_o != m.bot_x:
            grouped[m.bot_o].append(m)
    return grouped


async def enqueue_match_pairs(
    session: AsyncSession, new_bot_id: int, new_python_version: str
) -> None:
    """Enqueue one MatchJob per unplayed pair involving the newly inserted
    bot. Includes the self-pair (`new` vs `new`). The chosen Python version
    is `max(new, other)` so older bots run on newer interpreters."""
    queue = get_queue()
    result = await session.execute(select(Bot.id, Bot.python_version))
    rows = result.all()
    for other_id, other_py in rows:
        py = pick_python_version(new_python_version, other_py)
        await queue.enqueue_match(MatchJob(new_bot_id, other_id, py))
        if other_id != new_bot_id:
            await queue.enqueue_match(MatchJob(other_id, new_bot_id, py))
