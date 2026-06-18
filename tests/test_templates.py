"""Unit tests for web/templates.py helper functions.

These test the context dict passed to TemplateResponse directly, catching
mutations that change dict keys or replace the context with None."""

from unittest.mock import MagicMock, patch

from fastapi import Request

from web.templates import not_found, render_index_response


def _mock_request() -> MagicMock:
    return MagicMock(spec=Request)


# ---------------------------------------------------------------------------
# render_index_response — "bots" key must be present and start empty
# ---------------------------------------------------------------------------


def test_render_index_response_context_has_bots_key() -> None:
    with patch("web.templates.templates") as mock_templates:
        mock_templates.TemplateResponse = MagicMock()
        render_index_response(_mock_request())
    context = mock_templates.TemplateResponse.call_args[0][2]
    assert "bots" in context


def test_render_index_response_bots_starts_empty() -> None:
    with patch("web.templates.templates") as mock_templates:
        mock_templates.TemplateResponse = MagicMock()
        render_index_response(_mock_request())
    context = mock_templates.TemplateResponse.call_args[0][2]
    assert context["bots"] == []


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
