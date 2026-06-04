import ast
import json
import re
import secrets
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import Cookie, FastAPI, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import (
    get_bot_family,
    get_leaderboard,
    get_match,
    get_moves,
    get_next_version,
    get_owner_token,
    get_session,
    insert_bot,
    list_bot_names,
    list_bots,
    list_matches,
)
from db.models import Bot
from messaging import MatchJob, get_queue, pick_python_version

app = FastAPI()
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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


_VERSIONED_RE = re.compile(r"^(.+)V\d+$")

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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    async with get_session() as session:
        bots = await list_bots(session)
    return templates.TemplateResponse(request, "index.html", {"bots": bots})


@app.post("/submit", response_class=HTMLResponse)
async def submit_bot(
    request: Request,
    file: UploadFile = File(...),
    ttt_owned_bots: str | None = Cookie(default=None),
) -> HTMLResponse:
    source_bytes = await file.read()
    source = source_bytes.decode("utf-8", errors="replace")

    bot_name = extract_bot_name(source)
    if not bot_name:
        return _render(
            request,
            error="Your bot must start with a docstring containing 'name: <name>'.",
        )

    python_version = extract_python_version(source)
    if python_version is None:
        return _render(
            request,
            error=(
                "Invalid 'python:' value in docstring. "
                "Use a version like '3', '3.11', or '3.12'."
            ),
        )

    owned = parse_cookie(ttt_owned_bots)

    async with get_session() as session:
        base = implied_base(bot_name)
        if base and await get_owner_token(session, base) is not None:
            return _render(
                request,
                error=(
                    f"'{bot_name}' looks like a versioned name. "
                    f"Submit as '{base}' and versioning is handled automatically."
                ),
            )
        existing_token = await get_owner_token(session, bot_name)

        if existing_token:
            if owned.get(bot_name) != existing_token:
                return _render(
                    request,
                    error=f"'{bot_name}' is already taken by someone else.",
                )
            owner_token = existing_token
        else:
            owner_token = secrets.token_hex(32)

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
        new_bot_id = await _new_bot_id(session, name)
        await _enqueue_match_pairs(session, new_bot_id, python_version)
        bots = await list_bots(session)

    owned[bot_name] = owner_token
    response = templates.TemplateResponse(
        request,
        "index.html",
        {"bots": bots, "success": f"'{name}' submitted successfully!"},
    )
    response.set_cookie(
        key="ttt_owned_bots",
        value=urllib.parse.quote(json.dumps(owned), safe=""),
        httponly=True,
        samesite="lax",
    )
    return response


def _render(request: Request, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"bots": [], **ctx})


async def _new_bot_id(session: AsyncSession, versioned_name: str) -> int:
    result = await session.execute(
        select(Bot.id).where(Bot.versioned_name == versioned_name)
    )
    return result.scalar_one()


async def _enqueue_match_pairs(
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


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(request: Request) -> HTMLResponse:
    async with get_session() as session:
        rows = await get_leaderboard(session)
    return templates.TemplateResponse(request, "leaderboard.html", {"rows": rows})


@app.get("/matches", response_class=HTMLResponse)
async def matches(
    request: Request, bot: str | None = Query(default=None)
) -> HTMLResponse:
    async with get_session() as session:
        rows = await list_matches(session, bot_name=bot)
        bot_names = await list_bot_names(session)
    return templates.TemplateResponse(
        request,
        "matches.html",
        {"matches": rows, "bot_names": bot_names, "selected_bot": bot},
    )


@app.get("/bots/{base_name}", response_class=HTMLResponse)
async def bot_family(request: Request, base_name: str) -> HTMLResponse:
    async with get_session() as session:
        versions = await get_bot_family(session, base_name)
        if not versions:
            return templates.TemplateResponse(request, "404.html", {}, status_code=404)
        matches = await list_matches(session, bot_name=base_name)

    # Group matches by which version of this family participated.
    # A match where both sides are in the family (different versions) shows
    # up under both; a true self-match (same versioned_name on both sides)
    # shows up once.
    versioned_names = {v.versioned_name for v in versions}
    grouped: dict[str, list[Any]] = {v.versioned_name: [] for v in versions}
    for m in matches:
        if m.bot_x in versioned_names:
            grouped[m.bot_x].append(m)
        if m.bot_o in versioned_names and m.bot_o != m.bot_x:
            grouped[m.bot_o].append(m)

    return templates.TemplateResponse(
        request,
        "bot_detail.html",
        {"base_name": base_name, "versions": versions, "grouped_matches": grouped},
    )


@app.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail(request: Request, match_id: int) -> HTMLResponse:
    async with get_session() as session:
        match = await get_match(session, match_id)
        if match is None:
            return templates.TemplateResponse(request, "404.html", {}, status_code=404)
        moves = await get_moves(session, match_id)
    return templates.TemplateResponse(
        request, "match_detail.html", {"match": match, "moves": moves}
    )
