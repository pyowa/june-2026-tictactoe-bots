import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import web.main
from entities.bot.model import Bot
from tests.conftest import upload
from web.utils import extract_bot_name, extract_python_version


def read_owned_cookie(client: TestClient) -> dict:
    raw = client.cookies.get("ttt_owned_bots", "")
    return json.loads(urllib.parse.unquote(raw)) if raw else {}


async def _read_bot_field(engine: AsyncEngine, stmt):
    """Execute a SELECT and return the first row.

    Replaces the sync `with Session(engine) as session: session.execute(...)`
    pattern inside the test files now that the conftest fixture yields an
    `AsyncEngine`."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        result = await session.execute(stmt)
        return result


def test_fresh_submission_succeeds(client):
    resp = upload(client, "MyBot")
    assert resp.status_code == 200
    assert "MyBot" in resp.text
    assert "submitted successfully" in resp.text


def test_fresh_submission_sets_ownership_cookie(client):
    resp = upload(client, "MyBot")
    owned = read_owned_cookie(client)
    assert "MyBot" in owned
    assert len(owned["MyBot"]) == 64  # secrets.token_hex(32)
    # Assert security flags on the raw Set-Cookie header — `response.cookies`
    # is a parsed jar that doesn't preserve attribute flags reliably.
    set_cookie = resp.headers["set-cookie"]
    assert "ttt_owned_bots=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


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


def test_name_ending_in_v_digit_rejected_always(client):
    """Bot names ending in 'V<digits>' are reserved for the auto-versioning
    system — rejected even if no prior bot with the base exists."""
    resp = upload(client, "MyBotV9")
    assert "reserved for auto-versioning" in resp.text


def test_name_ending_in_v_digit_rejected_when_base_exists(client):
    upload(client, "TestBot")
    resp = upload(client, "TestBotV2")
    assert "reserved for auto-versioning" in resp.text


def test_name_with_v_digits_not_at_end_accepted(client):
    """'FooV2X' contains V<digits> but not at the end; not reserved."""
    resp = upload(client, "FooV2X")
    assert "submitted successfully" in resp.text


def test_name_with_no_v_accepted(client):
    resp = upload(client, "Bot1")
    assert "submitted successfully" in resp.text


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


async def test_python_version_defaults_when_omitted(client, engine):
    """Omitted `python:` field → defaults to the latest supported version."""
    from web.utils import DEFAULT_PYTHON_VERSION
    resp = upload(client, "MyBot")
    assert "submitted successfully" in resp.text
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (await session.execute(
            select(Bot.python_version).where(Bot.versioned_name == "MyBot")
        )).one()
    assert row[0] == DEFAULT_PYTHON_VERSION


@pytest.mark.parametrize("version", ["3.10", "3.11", "3.12", "3.13", "3.14"])
async def test_python_version_supported_versions_accepted(
    client, engine, version: str
) -> None:
    slug = version.replace(".", "_")
    body = f'"""\nname: V{slug}\npython: {version}\n"""\nimport sys\n'.encode()
    resp = client.post("/submit", files={"file": ("bot.py", body, "text/plain")})
    assert "submitted successfully" in resp.text
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (await session.execute(
            select(Bot.python_version).where(Bot.base_name.like("V%")).limit(1)
        )).first()
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
# extract_bot_name — unit tests for defensive branches
# ---------------------------------------------------------------------------


def test_extract_bot_name_syntax_error() -> None:
    assert extract_bot_name("def (:\n") is None


def test_extract_bot_name_empty_file() -> None:
    assert extract_bot_name("") is None


def test_extract_bot_name_non_string_constant() -> None:
    assert extract_bot_name("42\n") is None


def test_extract_bot_name_first_node_not_expr() -> None:
    assert extract_bot_name("import sys\n") is None


def test_extract_bot_name_empty_name_value_returns_none() -> None:
    source = '"""\nname:   \n"""\nimport sys\n'
    assert extract_bot_name(source) is None


def test_extract_bot_name_no_name_field_returns_none() -> None:
    source = '"""\npython: 3.12\n"""\nimport sys\n'
    assert extract_bot_name(source) is None


def test_extract_bot_name_returns_name_when_present() -> None:
    source = '"""\nname: MyBot\n"""\nimport sys\n'
    assert extract_bot_name(source) == "MyBot"


# ---------------------------------------------------------------------------
# DEFAULT_PYTHON_VERSION pin — keep the constant aligned with the supported tuple
# ---------------------------------------------------------------------------


def test_default_python_version_pins_to_last_supported() -> None:
    from web.utils import DEFAULT_PYTHON_VERSION, SUPPORTED_PYTHON_VERSIONS
    assert DEFAULT_PYTHON_VERSION == SUPPORTED_PYTHON_VERSIONS[-1]
    assert DEFAULT_PYTHON_VERSION == "3.14"


# ---------------------------------------------------------------------------
# parse_cookie — fallback when the value is not JSON
# ---------------------------------------------------------------------------


def test_parse_cookie_returns_empty_dict_on_invalid_json() -> None:
    from web.utils import parse_cookie
    assert parse_cookie("not-json") == {}


# ---------------------------------------------------------------------------
# Bot source persistence (stored in the DB; no filesystem involvement)
# ---------------------------------------------------------------------------


async def test_upload_stores_source_bytes_in_db(client, engine):
    upload(client, "MyBot", extra="x = 42  # source marker\n")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (await session.execute(
            select(Bot.source).where(Bot.versioned_name == "MyBot")
        )).one()
    stored = bytes(row[0])
    assert b"name: MyBot" in stored
    assert b"x = 42  # source marker" in stored


async def test_resubmit_stores_each_version_separately_in_db(client, engine):
    upload(client, "MyBot", extra="# v1 marker\n")
    upload(client, "MyBot", extra="# v2 marker\n")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(
            select(Bot.versioned_name, Bot.source)
            .where(Bot.base_name == "MyBot")
            .order_by(Bot.version)
        )).all()
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
