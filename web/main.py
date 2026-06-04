from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from db.database import (
    get_bot_family,
    get_leaderboard,
    get_match,
    get_moves,
    get_session,
    list_bot_names,
    list_bots,
    list_matches,
)
from messaging.client import make_queue
from messaging.queue import Queue
from web.dependencies import get_queue
from web.submit import handle_submission
from web.templates import not_found, templates
from web.utils import group_matches_by_version


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the process-wide queue at startup, close it at shutdown.
    Routes reach it via the `get_queue` dependency, tests substitute a fake
    via `app.dependency_overrides[get_queue]`."""
    queue = make_queue()
    app.state.queue = queue
    try:
        yield
    finally:
        await queue.close()


app = FastAPI(lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)


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
    queue: Queue = Depends(get_queue),
) -> HTMLResponse:
    return await handle_submission(request, file, ttt_owned_bots, queue)


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
            return not_found(request)
        matches = await list_matches(session, bot_name=base_name)
    grouped = group_matches_by_version(versions, matches)
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
            return not_found(request)
        moves = await get_moves(session, match_id)
    return templates.TemplateResponse(
        request, "match_detail.html", {"match": match, "moves": moves}
    )
