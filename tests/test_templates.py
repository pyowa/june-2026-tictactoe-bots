"""Unit tests for web/templates.py helper functions.

These test the context dict passed to TemplateResponse directly, catching
mutations that change dict keys or replace the context with None."""

from unittest.mock import MagicMock, patch

from fastapi import Request

from web.templates import not_found, render_submit_response


def _mock_request() -> MagicMock:
    return MagicMock(spec=Request)


# ---------------------------------------------------------------------------
# render_submit_response — passes ctx through unchanged.
# ---------------------------------------------------------------------------


def test_render_submit_response_passes_ctx_through() -> None:
    with patch("web.templates.templates") as mock_templates:
        mock_templates.TemplateResponse = MagicMock()
        render_submit_response(_mock_request(), success="ok!")
    context = mock_templates.TemplateResponse.call_args[0][2]
    assert context == {"success": "ok!"}


def test_render_submit_response_empty_ctx_is_empty_dict() -> None:
    with patch("web.templates.templates") as mock_templates:
        mock_templates.TemplateResponse = MagicMock()
        render_submit_response(_mock_request())
    context = mock_templates.TemplateResponse.call_args[0][2]
    assert context == {}


def test_render_submit_response_targets_submit_template() -> None:
    with patch("web.templates.templates") as mock_templates:
        mock_templates.TemplateResponse = MagicMock()
        render_submit_response(_mock_request())
    template_name = mock_templates.TemplateResponse.call_args[0][1]
    assert template_name == "submit.html"


# ---------------------------------------------------------------------------
# not_found — context must be {} (not None, not absent)
# ---------------------------------------------------------------------------


def test_not_found_context_is_empty_dict() -> None:
    with patch("web.templates.templates") as mock_templates:
        mock_templates.TemplateResponse = MagicMock()
        not_found(_mock_request())
    args = mock_templates.TemplateResponse.call_args[0]
    # Third positional arg is the context dict; must exist and be {}
    assert len(args) >= 3, "context arg missing from TemplateResponse call"
    assert args[2] == {}
    assert args[2] is not None
