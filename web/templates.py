"""Jinja2 template instance and small response helpers that wrap it.

Centralizes the `templates` object so route handlers and the submission flow
both render through the same configuration. Routes that need to render
arbitrary templates can still call `templates.TemplateResponse(...)`
directly; the helpers here exist for the recurring patterns (rendering the
submit page with extra context, returning a 404, embedding the sample bot
files on the home page)."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def read_template_sample(filename: str) -> str:
    """Read a user-facing Python sample (e.g. `template_bot.py`) from the
    templates directory so the home page can embed it verbatim."""
    return (_TEMPLATES_DIR / filename).read_text()


def render_submit_response(request: Request, **ctx: Any) -> HTMLResponse:
    """Render `submit.html` with any extra context (e.g. error/success banner).

    Used by the upload flow when the page needs a status message rendered
    above the form."""
    return templates.TemplateResponse(
        request,
        "submit.html",  # pragma: no mutate -- macOS FS masks case
        ctx,
    )


def not_found(request: Request) -> HTMLResponse:
    """Standard 404 response used by routes whose path parameter doesn't
    resolve to a known entity."""
    return templates.TemplateResponse(
        request,
        "404.html",  # pragma: no mutate -- macOS FS masks case
        {},
        status_code=404,
    )
