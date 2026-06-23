import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aio_pika
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import db.session
from entities.bot.repository import BotRepository
from entities.match.repository import MatchRepository
from entities.move.repository import MoveRepository
from messaging.client import BROKER_URL, make_queue
from messaging.contracts import BUILD_POD_QUEUE, EVENTS_EXCHANGE
from messaging.health import broker_check, db_check, make_health_router
from messaging.log import configure_logging
from messaging.queue import Queue
from web.dependencies import get_bots, get_matches, get_moves, get_queue
from web.play import (
    TurnRequest,
    handle_play_turn,
    handle_set_player_name,
    render_play_page,
    render_play_vs_page,
)
from web.submit import handle_submission
from web.templates import not_found, read_template_sample, templates
from web.utils import group_matches_by_version


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the process-wide queue at startup, close it at shutdown.
    Routes reach it via the `get_queue` dependency, tests substitute a fake
    via `app.dependency_overrides[get_queue]`."""
    configure_logging()
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
app.include_router(
    make_health_router(
        {
            "db": db_check(db.session.session_factory),
            "broker": broker_check(BROKER_URL, BUILD_POD_QUEUE),
        }
    )
)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "bot_template": read_template_sample("template_bot.py"),
            "test_template": read_template_sample("test_template_bot.py"),
        },
    )


@app.get("/submit", response_class=HTMLResponse)
async def submit_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "submit.html", {})


@app.post("/submit", response_class=HTMLResponse)
async def submit_bot(
    request: Request,
    file: UploadFile = File(...),
    ttt_owned_bots: str | None = Cookie(default=None),
    queue: Queue = Depends(get_queue),
    bots: BotRepository = Depends(get_bots),
) -> HTMLResponse:
    return await handle_submission(request, file, ttt_owned_bots, queue, bots)


@app.get("/play", response_class=HTMLResponse)
async def play(
    request: Request,
    ttt_player_name: str | None = Cookie(default=None),
    bots: BotRepository = Depends(get_bots),
) -> HTMLResponse:
    return await render_play_page(request, ttt_player_name, bots)


@app.post("/play/name", response_class=HTMLResponse)
async def play_name(
    request: Request,
    player_name: str = Form(...),
) -> HTMLResponse:
    return handle_set_player_name(request, player_name)


@app.get("/play/vs/{bot_id}", response_class=HTMLResponse)
async def play_vs(
    request: Request,
    bot_id: int,
    ttt_player_name: str | None = Cookie(default=None),
    bots: BotRepository = Depends(get_bots),
) -> Response:
    return await render_play_vs_page(request, bot_id, ttt_player_name, bots)


@app.post("/play/turn")
async def play_turn(
    request: Request,
    payload: TurnRequest,
    bots: BotRepository = Depends(get_bots),
) -> Response:
    return await handle_play_turn(request, payload, bots)


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(
    request: Request, bots: BotRepository = Depends(get_bots)
) -> HTMLResponse:
    rows = await bots.leaderboard()
    return templates.TemplateResponse(request, "leaderboard.html", {"rows": rows})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request, bots: BotRepository = Depends(get_bots)
) -> HTMLResponse:
    """Event-mode dashboard: shows the host's LAN URL in big bold font for
    others to type/scan, with the live-polling leaderboard underneath.

    `HOST_IP` is injected onto the web Deployment by `make reload-web` —
    auto-detected from the host's Wi-Fi interface (en0)."""
    rows = await bots.leaderboard()
    host_ip = os.environ.get("HOST_IP", "").strip() or None
    return templates.TemplateResponse(
        request, "dashboard.html", {"rows": rows, "host_ip": host_ip}
    )


@app.websocket("/dashboard/ws")
async def dashboard_ws(websocket: WebSocket) -> None:
    """Stream `ttt.events` fanout messages to a dashboard tab. Each tab gets
    its own exclusive auto-delete queue bound to the events exchange so all
    open dashboards receive every event."""
    await websocket.accept()
    connection = None
    try:
        connection = await aio_pika.connect_robust(BROKER_URL)
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=False
        )
        queue = await channel.declare_queue(exclusive=True, auto_delete=True)
        await queue.bind(exchange)
        async with queue.iterator() as it:
            async for message in it:
                async with message.process():
                    try:
                        await websocket.send_text(message.body.decode())
                    except WebSocketDisconnect:
                        return
                    except RuntimeError:
                        # Starlette raises RuntimeError if we try to send on a
                        # closed websocket. Treat the same as disconnect.
                        return
    except WebSocketDisconnect:
        pass
    finally:
        if connection is not None and not connection.is_closed:
            await connection.close()


@app.get("/matches", response_class=HTMLResponse)
async def matches(
    request: Request, matches: MatchRepository = Depends(get_matches)
) -> HTMLResponse:
    rows = await matches.list_all()
    return templates.TemplateResponse(request, "matches.html", {"matches": rows})


@app.get("/bots/{base_name}", response_class=HTMLResponse)
async def bot_family(
    request: Request,
    base_name: str,
    bots: BotRepository = Depends(get_bots),
    matches: MatchRepository = Depends(get_matches),
) -> HTMLResponse:
    versions = await bots.family(base_name)
    if not versions:
        return not_found(request)
    match_rows = await matches.list_for_bot(base_name)
    grouped = group_matches_by_version(versions, match_rows)
    return templates.TemplateResponse(
        request,
        "bot_detail.html",
        {"base_name": base_name, "versions": versions, "grouped_matches": grouped},
    )


@app.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail(
    request: Request,
    match_id: int,
    matches: MatchRepository = Depends(get_matches),
    moves: MoveRepository = Depends(get_moves),
) -> HTMLResponse:
    return await _render_match(
        request, match_id, "/matches", "Back to matches", matches, moves
    )


@app.get("/bots/{base_name}/matches/{match_id}", response_class=HTMLResponse)
async def bot_match_detail(
    request: Request,
    base_name: str,
    match_id: int,
    matches: MatchRepository = Depends(get_matches),
    moves: MoveRepository = Depends(get_moves),
) -> HTMLResponse:
    return await _render_match(
        request,
        match_id,
        f"/bots/{base_name}",
        f"Back to {base_name}",
        matches,
        moves,
        bot_base_name=base_name,
    )


async def _render_match(
    request: Request,
    match_id: int,
    back_url: str,
    back_label: str,
    matches: MatchRepository,
    moves: MoveRepository,
    bot_base_name: str | None = None,
) -> HTMLResponse:
    match = await matches.by_id(match_id, bot_base_name=bot_base_name)
    if match is None:
        return not_found(request)
    move_rows = await moves.for_match(match_id)
    moves_json = json.dumps(
        [
            {
                "move_number": m.move_number,
                "bot_name": m.bot_name,
                "board_state": m.board_state,
                "error": m.error,
            }
            for m in move_rows
        ]
    )
    return templates.TemplateResponse(
        request,
        "match_detail.html",  # pragma: no mutate -- macOS FS masks case mutation
        {
            "match": match,
            "moves": move_rows,
            "moves_json": moves_json,
            "back_url": back_url,
            "back_label": back_label,
        },
    )
