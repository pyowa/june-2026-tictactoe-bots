import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import web.main
from tests.conftest import upload
from web.utils import extract_python_version


def read_owned_cookie(client: TestClient) -> dict:
    raw = client.cookies.get("ttt_owned_bots", "")
    return json.loads(urllib.parse.unquote(raw)) if raw else {}


def test_fresh_submission_succeeds(client):
    resp = upload(client, "MyBot")
    assert resp.status_code == 200
    assert "MyBot" in resp.text
    assert "submitted successfully" in resp.text


def test_fresh_submission_sets_ownership_cookie(client):
    upload(client, "MyBot")
    owned = read_owned_cookie(client)
    assert "MyBot" in owned
    assert len(owned["MyBot"]) == 64  # secrets.token_hex(32)


def test_missing_docstring_rejected(client):
    resp = client.post(
        "/submit", files={"file": ("bot.py", b"import sys\n", "text/plain")}
    )
    assert "docstring" in resp.text


def test_docstring_without_name_field_rejected(client):
    source = b'"""This bot does stuff but has no name field."""\nimport sys\n'
    resp = client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    assert "docstring" in resp.text


def test_name_taken_without_cookie_rejected(client):
    upload(client, "MyBot")
    # Fresh client has no ownership cookie but shares the same DB.
    with TestClient(web.main.app) as fresh_client:
        resp = upload(fresh_client, "MyBot")
    assert "already taken by someone else" in resp.text


def test_resubmit_with_cookie_creates_v2(client):
    upload(client, "MyBot")
    resp = upload(client, "MyBot")
    assert "MyBotV2" in resp.text
    assert "submitted successfully" in resp.text


def test_resubmit_multiple_times_increments_version(client):
    upload(client, "MyBot")
    upload(client, "MyBot")
    resp = upload(client, "MyBot")
    assert "MyBotV3" in resp.text
    assert "submitted successfully" in resp.text


def test_versioned_bots_all_appear_in_listing(client):
    upload(client, "MyBot")
    upload(client, "MyBot")
    resp = client.get("/")
    assert "MyBot" in resp.text
    assert "MyBotV2" in resp.text


def test_different_bots_owned_independently(client):
    upload(client, "AlphaBot")
    upload(client, "BetaBot")
    owned = read_owned_cookie(client)
    assert "AlphaBot" in owned
    assert "BetaBot" in owned


def test_versioned_name_rejected_when_base_exists(client):
    upload(client, "TestBot")
    resp = upload(client, "TestBotV2")
    assert "versioned name" in resp.text


def test_versioned_name_allowed_when_base_does_not_exist(client):
    resp = upload(client, "TestBotV2")
    assert "submitted successfully" in resp.text


def test_versioned_name_rejected_for_higher_versions(client):
    upload(client, "TestBot")
    resp = upload(client, "TestBotV10")
    assert "versioned name" in resp.text


def test_syntax_error_in_source_rejected(client):
    resp = client.post(
        "/submit",
        files={"file": ("bot.py", b"def (:\n", "text/plain")},
    )
    assert "docstring" in resp.text


def test_empty_file_rejected(client):
    resp = client.post(
        "/submit",
        files={"file": ("bot.py", b"", "text/plain")},
    )
    assert "docstring" in resp.text


def test_non_string_constant_at_module_level_rejected(client):
    resp = client.post(
        "/submit",
        files={"file": ("bot.py", b"42\n", "text/plain")},
    )
    assert "docstring" in resp.text


def test_malformed_cookie_is_ignored(client):
    client.cookies.set("ttt_owned_bots", "not-valid-json%ZZ")
    resp = upload(client, "MyBot")
    assert "submitted successfully" in resp.text


def test_python_version_defaults_when_omitted(client, engine):
    """Omitted `python:` field → defaults to the latest supported version."""
    from sqlalchemy import text

    from web.utils import DEFAULT_PYTHON_VERSION
    resp = upload(client, "MyBot")
    assert "submitted successfully" in resp.text
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT python_version FROM bots WHERE versioned_name = 'MyBot'")
        ).first()
    assert row is not None
    assert row[0] == DEFAULT_PYTHON_VERSION


@pytest.mark.parametrize("version", ["3.10", "3.11", "3.12", "3.13", "3.14"])
def test_python_version_supported_versions_accepted(
    client, engine, version: str
) -> None:
    from sqlalchemy import text
    slug = version.replace(".", "_")
    body = f'"""\nname: V{slug}\npython: {version}\n"""\nimport sys\n'.encode()
    resp = client.post("/submit", files={"file": ("bot.py", body, "text/plain")})
    assert "submitted successfully" in resp.text
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT python_version FROM bots WHERE base_name LIKE 'V%' LIMIT 1")
        ).first()
    assert row is not None
    assert row[0] == version


@pytest.mark.parametrize(
    "version",
    [
        "3",       # bare major — ambiguous, rejected
        "3.9",     # too old
        "3.15",    # not yet supported
        "4.0",     # not a real Python
        "2.7",     # not supported
        "latest",  # not a version string
        "3.11.4",  # patch-level not supported
        "",        # empty
    ],
)
def test_python_version_unsupported_versions_rejected(client, version: str) -> None:
    source = f'"""\nname: MyBot\npython: {version}\n"""\nimport sys\n'.encode()
    resp = client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    assert "Invalid" in resp.text
    assert "python:" in resp.text


# ---------------------------------------------------------------------------
# extract_python_version — unit tests for defensive branches
# ---------------------------------------------------------------------------


def test_extract_python_version_syntax_error() -> None:
    assert extract_python_version("def (:\n") is None


def test_extract_python_version_empty_file() -> None:
    assert extract_python_version("") is None


def test_extract_python_version_non_string_constant() -> None:
    assert extract_python_version("42\n") is None


def test_extract_python_version_first_node_not_expr() -> None:
    assert extract_python_version("import sys\n") is None


def test_extract_python_version_no_python_field_returns_default() -> None:
    from web.utils import DEFAULT_PYTHON_VERSION
    source = '"""\nname: MyBot\n"""\nimport sys\n'
    assert extract_python_version(source) == DEFAULT_PYTHON_VERSION


# ---------------------------------------------------------------------------
# Bot source persistence (stored in the DB; no filesystem involvement)
# ---------------------------------------------------------------------------


def test_upload_stores_source_bytes_in_db(client, engine):
    from sqlalchemy import text
    upload(client, "MyBot", extra="x = 42  # source marker\n")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT source FROM bots WHERE versioned_name = 'MyBot'")
        ).first()
    assert row is not None
    stored = bytes(row[0])
    assert b"name: MyBot" in stored
    assert b"x = 42  # source marker" in stored


def test_resubmit_stores_each_version_separately_in_db(client, engine):
    from sqlalchemy import text
    upload(client, "MyBot", extra="# v1 marker\n")
    upload(client, "MyBot", extra="# v2 marker\n")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT versioned_name, source FROM bots "
                "WHERE base_name = 'MyBot' ORDER BY version"
            )
        ).fetchall()
    assert len(rows) == 2
    assert rows[0].versioned_name == "MyBot"
    assert rows[1].versioned_name == "MyBotV2"
    assert b"# v1 marker" in bytes(rows[0].source)
    assert b"# v2 marker" in bytes(rows[1].source)


# ---------------------------------------------------------------------------
# Match queue enqueue behavior
# ---------------------------------------------------------------------------


def test_first_upload_enqueues_only_self_pair(client, mock_queue):
    from web.utils import DEFAULT_PYTHON_VERSION
    upload(client, "Solo")
    assert len(mock_queue.messages) == 1
    job = mock_queue.messages[0]
    assert job.bot_x_id == job.bot_o_id  # self-pair
    assert job.python_version == DEFAULT_PYTHON_VERSION


def test_second_upload_enqueues_three_jobs(client, mock_queue):
    """With two bots in the DB, a new upload should produce:
       self-pair + (new vs existing) + (existing vs new) = 3 messages."""
    upload(client, "Alpha")
    mock_queue.messages.clear()  # ignore Alpha's own self-pair
    upload(client, "Beta")
    pairs = {(m.bot_x_id, m.bot_o_id) for m in mock_queue.messages}
    assert len(pairs) == 3
    # Beta's id is 2, Alpha is 1.
    assert (2, 2) in pairs  # self
    assert (2, 1) in pairs  # Beta as X
    assert (1, 2) in pairs  # Beta as O


def test_enqueue_picks_higher_python_version(client, mock_queue):
    """When two bots declare different python versions, the higher one wins."""
    client.post(
        "/submit",
        files={
            "file": (
                "alpha.py",
                b'"""\nname: Alpha\npython: 3.11\n"""\nimport sys\n',
                "text/plain",
            )
        },
    )
    mock_queue.messages.clear()
    client.post(
        "/submit",
        files={
            "file": (
                "beta.py",
                b'"""\nname: Beta\npython: 3.13\n"""\nimport sys\n',
                "text/plain",
            )
        },
    )
    # Every cross-pair message should be tagged with the higher version.
    cross = [m for m in mock_queue.messages if m.bot_x_id != m.bot_o_id]
    assert cross, "expected at least one cross-pair message"
    for m in cross:
        assert m.python_version == "3.13"


