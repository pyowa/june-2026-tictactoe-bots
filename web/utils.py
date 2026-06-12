"""Pure helpers used by the web layer.

Anything in this module should be free of FastAPI / template / response I/O
so it can be unit-tested in isolation. The async helper here touches the DB
session and the message queue, but only via the objects passed in by the
caller (no global FastAPI state).
"""

import ast
import json
import secrets
import urllib.parse
from typing import Any

from entities.bot.repository import BotRepository
from messaging.queue import MatchJob, Queue
from messaging.routing import pick_runtime_key
from web.runtimes import DEFAULT_RUNTIME_KEY, RUNTIMES

# Derived from RUNTIMES so the two stay in sync automatically.
SUPPORTED_PYTHON_VERSIONS: tuple[str, ...] = tuple(
    key[len("python-"):] for key in RUNTIMES if key.startswith("python-")
)
DEFAULT_PYTHON_VERSION: str = DEFAULT_RUNTIME_KEY[len("python-"):]


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


def extract_runtime_key(source: str) -> str | None:
    """Extract the runtime key from a bot docstring.

    Accepts `language: python-3.13` (primary) or `python: 3.13` (legacy alias
    that maps to `python-3.13`). Returns None if an unrecognised key is given,
    or DEFAULT_RUNTIME_KEY when neither field is present.
    """
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

    language_key: str | None = None
    python_ver: str | None = None

    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("language:"):
            language_key = stripped[9:].strip()
        elif stripped.lower().startswith("python:"):
            python_ver = stripped[7:].strip()

    if language_key is not None:
        return language_key if language_key in RUNTIMES else None

    if python_ver is not None:
        mapped = f"python-{python_ver}"
        return mapped if mapped in RUNTIMES else None

    return DEFAULT_RUNTIME_KEY


def extract_python_version(source: str) -> str | None:
    """Legacy wrapper — returns the Python version string (e.g. '3.13') for
    Python runtimes, or None if the runtime key is invalid/non-Python."""
    key = extract_runtime_key(source)
    if key is None:
        return None
    return key[len("python-"):] if key.startswith("python-") else None


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
    return urllib.parse.quote(json.dumps(owned), safe="")  # pragma: no mutate


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


def _python_version_from_runtime_key(key: str) -> str:
    """'python-3.13' → '3.13'. Non-Python runtimes return the full key."""
    return key[len("python-"):] if key.startswith("python-") else key


async def enqueue_match_pairs(
    queue: Queue,
    bots: BotRepository,
    new_bot_id: int,
    new_runtime_key: str,
) -> int:
    """Enqueue one MatchJob per unplayed pair involving the newly inserted
    bot. Includes the self-pair (`new` vs `new`). The chosen runtime is the
    higher of the two bots' declared runtimes so older bots run on newer
    interpreters. Returns the number of jobs enqueued."""
    all_bots = await bots.all()
    count = 0
    for other in all_bots:
        rk = pick_runtime_key(new_runtime_key, other.runtime_key)
        py = _python_version_from_runtime_key(rk)
        await queue.enqueue_match(
            MatchJob(
                bot_x_id=new_bot_id,
                bot_o_id=other.id,
                python_version=py,
                runtime_key=rk,
                correlation_id=secrets.token_hex(16),
            )
        )
        count += 1
        if other.id != new_bot_id:
            await queue.enqueue_match(
                MatchJob(
                    bot_x_id=other.id,
                    bot_o_id=new_bot_id,
                    python_version=py,
                    runtime_key=rk,
                    correlation_id=secrets.token_hex(16),
                )
            )
            count += 1
    return count
