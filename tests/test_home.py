"""Tests for the new tabbed home page and the standalone submit page.

The home page reads two example files (`web/templates/template_bot.py` and
`web/templates/test_template_bot.py`) and embeds their content for visitors
to copy. These tests verify the file-loader helper *and* that the loaded
content actually reaches the rendered response — a regression here would
quietly ship an empty/broken onboarding page.
"""

from web.templates import read_template_sample

# ---------------------------------------------------------------------------
# Sample loader — direct unit tests on the file-reading helper.
# ---------------------------------------------------------------------------


def test_read_template_sample_returns_bot_template_contents() -> None:
    content = read_template_sample("template_bot.py")
    assert "Your Bot Name" in content
    assert "import sys" in content


def test_read_template_sample_returns_test_template_contents() -> None:
    content = read_template_sample("test_template_bot.py")
    assert "test_bot_makes_a_move_on_empty_board" in content
    assert "test_bot_blocks_opponent_about_to_win" in content


# ---------------------------------------------------------------------------
# GET / — tabbed home page embeds both example files.
# ---------------------------------------------------------------------------


def test_home_page_returns_200(client) -> None:
    resp = client.get("/")
    assert resp.status_code == 200


def test_home_page_embeds_bot_template_source(client) -> None:
    """The contract sample from web/templates/template_bot.py must reach the
    rendered HTML. Distinct phrase chosen so HTML-escape doesn't mangle it."""
    resp = client.get("/")
    assert "Your Bot Name" in resp.text


def test_home_page_embeds_test_template_source(client) -> None:
    """The pytest sample from web/templates/test_template_bot.py must reach
    the rendered HTML."""
    resp = client.get("/")
    assert "test_bot_makes_a_move_on_empty_board" in resp.text


def test_home_page_has_both_tab_controls(client) -> None:
    """Both tabs (`code` and `tests`) must be present so the user can switch."""
    resp = client.get("/")
    assert 'data-tab="code"' in resp.text
    assert 'data-tab="tests"' in resp.text


# ---------------------------------------------------------------------------
# GET /submit — the form is now its own page.
# ---------------------------------------------------------------------------


def test_submit_page_returns_200(client) -> None:
    resp = client.get("/submit")
    assert resp.status_code == 200


def test_submit_page_has_upload_form(client) -> None:
    resp = client.get("/submit")
    assert 'action="/submit"' in resp.text
    assert 'type="file"' in resp.text
