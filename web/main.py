import ast
import json
import re
import secrets
import sys
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import Cookie, FastAPI, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import (
    DB_PATH,
    get_leaderboard,
    get_match,
    get_moves,
    get_next_version,
    get_owner_token,
    init_db,
    insert_bot,
    list_bot_names,
    list_bots,
    list_matches,
)

BOTS_DIR = Path(__file__).parent.parent / "bots"
BOTS_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        bots = await list_bots(db)
    return templates.TemplateResponse(request, "index.html", {"bots": bots})


@app.post("/submit", response_class=HTMLResponse)
async def submit_bot(
    request: Request,
    file: UploadFile = File(...),
    ttt_owned_bots: str | None = Cookie(default=None),
) -> HTMLResponse:
    source = (await file.read()).decode("utf-8", errors="replace")

    bot_name = extract_bot_name(source)
    if not bot_name:
        return _render(
            request,
            error="Your bot must start with a docstring containing 'name: <name>'.",
        )

    owned = parse_cookie(ttt_owned_bots)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        base = implied_base(bot_name)
        if base and await get_owner_token(db, base) is not None:
            return _render(
                request,
                error=(
                    f"'{bot_name}' looks like a versioned name. "
                    f"Submit as '{base}' and versioning is handled automatically."
                ),
            )
        existing_token = await get_owner_token(db, bot_name)

        if existing_token:
            if owned.get(bot_name) != existing_token:
                return _render(
                    request,
                    error=f"'{bot_name}' is already taken by someone else.",
                )
            owner_token = existing_token
        else:
            owner_token = secrets.token_hex(32)

        version = await get_next_version(db, bot_name)
        name = versioned_name(bot_name, version)
        file_path = BOTS_DIR / f"{name}.py"
        file_path.write_text(source)
        await insert_bot(db, bot_name, name, version, owner_token, str(file_path))
        bots = await list_bots(db)

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


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(request: Request) -> HTMLResponse:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await get_leaderboard(db)
    return templates.TemplateResponse(request, "leaderboard.html", {"rows": rows})


@app.get("/matches", response_class=HTMLResponse)
async def matches(
    request: Request, bot: str | None = Query(default=None)
) -> HTMLResponse:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await list_matches(db, bot_name=bot)
        bot_names = await list_bot_names(db)
    return templates.TemplateResponse(
        request,
        "matches.html",
        {"matches": rows, "bot_names": bot_names, "selected_bot": bot},
    )


@app.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail(request: Request, match_id: int) -> HTMLResponse:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        match = await get_match(db, match_id)
        if match is None:
            return templates.TemplateResponse(request, "404.html", {}, status_code=404)
        moves = await get_moves(db, match_id)
    return templates.TemplateResponse(
        request, "match_detail.html", {"match": match, "moves": moves}
    )
