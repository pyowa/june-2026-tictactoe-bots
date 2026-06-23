"""Human-vs-bot play handlers.

The flow: a logged-in human picks a bot from /play, lands on /play/vs/{id},
then alternates turns with the bot. Board + turn state lives in the browser;
the server only assigns the human's symbol once (per visit) and responds to
each individual /play/turn submission.

Each top-level coroutine maps to a route registered in `web/main.py`."""

import asyncio
import secrets

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from entities.bot.repository import BotRepository
from web import bot_client
from web.bot_client import BotForfeit
from web.templates import not_found, templates

PLAYER_NAME_COOKIE = "ttt_player_name"


class TurnRequest(BaseModel):
    bot_id: int
    bot_symbol: str = Field(pattern="^[XO]$")
    board: str


async def render_play_page(
    request: Request,
    ttt_player_name: str | None,
    bots: BotRepository,
) -> HTMLResponse:
    """Render either the name form (no cookie) or the bot picker (cookie set)."""
    if not ttt_player_name:
        return templates.TemplateResponse(request, "play_name.html", {})
    rows = await bots.ready_bots_for_play()
    return templates.TemplateResponse(
        request,
        "play.html",
        {"player_name": ttt_player_name, "bots": rows},
    )


def handle_set_player_name(
    request: Request,
    player_name: str,
) -> HTMLResponse:
    """Validate the submitted name, set the cookie, and render the bot picker.

    Surrounding whitespace is stripped before storage. An empty/blank name
    re-renders the name form with an error and no cookie is set."""
    cleaned = player_name.strip()
    if not cleaned:
        return templates.TemplateResponse(
            request,
            "play_name.html",
            {"error": "Please enter a display name."},
        )
    response = templates.TemplateResponse(
        request,
        "play_name_set.html",
        {"player_name": cleaned},
    )
    response.set_cookie(
        key=PLAYER_NAME_COOKIE,
        value=cleaned,
        httponly=True,
        samesite="lax",
    )
    return response


async def render_play_vs_page(
    request: Request,
    bot_id: int,
    ttt_player_name: str | None,
    bots: BotRepository,
) -> Response:
    """Render the per-bot game page, or 404 / redirect when the prerequisites
    aren't met.

    Bounces to /play when there's no player-name cookie so the user can set
    one. 404s if the bot doesn't exist or hasn't reached `pod_ready=True`.
    Randomizes the human's symbol via secrets.choice and bakes it into the
    template as a data attribute so the client-side JS can drive turn order."""
    if not ttt_player_name:
        return RedirectResponse("/play", status_code=303)
    bot = await bots.by_id(bot_id)
    if bot is None or not bot.pod_ready:
        return not_found(request)
    human_symbol = secrets.choice(["X", "O"])
    bot_symbol = "O" if human_symbol == "X" else "X"
    return templates.TemplateResponse(
        request,
        "play_game.html",
        {
            "bot_id": bot_id,
            "bot_name": bot.versioned_name,
            "player_name": ttt_player_name,
            "human_symbol": human_symbol,
            "bot_symbol": bot_symbol,
        },
    )


async def handle_play_turn(
    request: Request,
    payload: TurnRequest,
    bots: BotRepository,
) -> Response:
    """Forward the turn request to the bot pod and return its new board.

    Failure modes map to `{"error": "<reason>"}` responses with the same 200
    status so the client-side JS can render the matching "Game over: ..."
    caption without distinguishing transport-level vs game-level failures."""
    bot = await bots.by_id(payload.bot_id)
    if bot is None or not bot.pod_ready or bot.pod_name is None:
        return not_found(request)

    pod_name = bot.pod_name
    symbol = payload.bot_symbol
    board = payload.board

    def _call_bot() -> str:
        core_v1 = bot_client.get_core_v1()
        ip = bot_client.get_pod_ip(core_v1, pod_name)
        if ip is None:
            raise BotForfeit("Bot is unavailable")
        return bot_client.request_bot_turn(ip, symbol, board)

    loop = asyncio.get_running_loop()
    try:
        new_board = await loop.run_in_executor(None, _call_bot)
    except BotForfeit as err:
        return _json_error(err.reason)
    return _json_board(new_board)


def _json_board(board: str) -> Response:
    """Return a JSON `{"board": ...}` response."""
    from fastapi.responses import JSONResponse

    return JSONResponse({"board": board})


def _json_error(reason: str) -> Response:
    """Return a JSON `{"error": "<reason>"}` response for a bot forfeit."""
    from fastapi.responses import JSONResponse

    return JSONResponse({"error": reason})
