"""Jinja2 template instance and small response helpers that wrap it.

Centralizes the `templates` object so route handlers and the submission flow
both render through the same configuration. Routes that need to render
arbitrary templates can still call `templates.TemplateResponse(...)`
directly; the helpers here exist for the recurring patterns (rendering the
index page with extra context, returning a 404)."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def render_index_response(request: Request, **ctx: Any) -> HTMLResponse:
    """Render `index.html` with an empty bot list plus any extra context.

    Used for submission-error and submission-success flows where the page
    needs to show a message banner above the (empty/refreshed) listing."""
    return templates.TemplateResponse(request, "index.html", {"bots": [], **ctx})


def not_found(request: Request) -> HTMLResponse:
    """Standard 404 response used by routes whose path parameter doesn't
    resolve to a known entity."""
    return templates.TemplateResponse(request, "404.html", {}, status_code=404)
