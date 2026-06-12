import html
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from structlog.testing import capture_logs

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
        row = (
            await session.execute(
                select(Bot.python_version).where(Bot.versioned_name == "MyBot")
            )
        ).one()
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
        row = (
            await session.execute(
                select(Bot.python_version).where(Bot.base_name.like("V%")).limit(1)
            )
        ).first()
    assert row is not None
    assert row[0] == version


@pytest.mark.parametrize(
    "version",
    [
        "3",  # bare major — ambiguous, rejected
        "3.9",  # too old
        "3.15",  # not yet supported
        "4.0",  # not a real Python
        "2.7",  # not supported
        "latest",  # not a version string
        "3.11.4",  # patch-level not supported
        "",  # empty
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
        row = (
            await session.execute(
                select(Bot.source).where(Bot.versioned_name == "MyBot")
            )
        ).one()
    stored = bytes(row[0])
    assert b"name: MyBot" in stored
    assert b"x = 42  # source marker" in stored


async def test_resubmit_stores_each_version_separately_in_db(client, engine):
    upload(client, "MyBot", extra="# v1 marker\n")
    upload(client, "MyBot", extra="# v2 marker\n")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Bot.versioned_name, Bot.source)
                .where(Bot.base_name == "MyBot")
                .order_by(Bot.version)
            )
        ).all()
    assert len(rows) == 2
    assert rows[0].versioned_name == "MyBot"
    assert rows[1].versioned_name == "MyBotV2"
    assert b"# v1 marker" in bytes(rows[0].source)
    assert b"# v2 marker" in bytes(rows[1].source)


# ---------------------------------------------------------------------------
# Match queue enqueue behavior
# ---------------------------------------------------------------------------


def test_upload_logs_bot_uploaded_event(client, mock_queue) -> None:
    from web.runtimes import DEFAULT_RUNTIME_KEY

    with capture_logs() as cap:
        upload(client, "LogBot")
    uploaded = [e for e in cap if e["event"] == "bot_uploaded"]
    assert len(uploaded) == 1
    assert uploaded[0]["bot_name"] == "LogBot"
    assert uploaded[0]["runtime_key"] == DEFAULT_RUNTIME_KEY
    assert "bot_id" in uploaded[0]
    assert "python_version" in uploaded[0]


# ---------------------------------------------------------------------------
# _SubmissionError — base class wiring
# ---------------------------------------------------------------------------


def test_submission_error_str_repr_equals_message() -> None:
    """_SubmissionError must pass message to super().__init__ so str(exc) works."""
    from web.submit import _SubmissionError

    exc = _SubmissionError("test message")
    assert exc.args == ("test message",)
    assert str(exc) == "test message"


# ---------------------------------------------------------------------------
# Source decoding — errors="replace" must be present
# ---------------------------------------------------------------------------


def test_invalid_utf8_source_decoded_with_replacement_not_raised(client) -> None:
    """Bytes that aren't valid UTF-8 must be decoded with U+FFFD, not raise.
    If errors='replace' is dropped, decode() uses strict mode and raises
    UnicodeDecodeError, crashing the handler.
    The invalid bytes must be inside a string literal so the resulting Python
    source (with replacement chars) is still syntactically valid."""
    source = b'"""\nname: BadBytes\n"""\nx = "bad\xff\xfe"\n'
    resp = client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    assert "submitted successfully" in resp.text


# ---------------------------------------------------------------------------
# Exact error message text — kills string-mangling / case mutants
# ---------------------------------------------------------------------------


def test_missing_name_error_message_exact(client) -> None:
    source = b'"""This bot has no name field."""\nimport sys\n'
    resp = client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    # Jinja2 HTML-escapes the message; unescape before comparing.
    # The banner element renders as: <div class="banner banner-error">MESSAGE</div>
    # Mutations that wrap the message string with "XX...XX" produce ">XXYour bot..."
    # instead of ">Your bot...", so we anchor the check to the element boundary.
    text = html.unescape(resp.text)
    assert 'banner-error">Your bot must start with a docstring' in text


def test_reserved_name_error_message_exact(client) -> None:
    resp = upload(client, "FooV9")
    # The reserved-name message ends with "which is reserved for auto-versioning..."
    # Mutations that wrap "reserved for auto-versioning" with "XX" produce
    # "which is XXreserved..." — checking the fixed prefix "which is reserved"
    # pins the exact word boundary.
    text = html.unescape(resp.text)
    assert "which is reserved for auto-versioning. Pick a different name." in text


def test_invalid_python_version_error_message_exact(client) -> None:
    source = b'"""\nname: MyBot\npython: 3.9\n"""\nimport sys\n'
    resp = client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    text = html.unescape(resp.text)
    assert "banner-error\">Invalid runtime in docstring." in text
    assert "'language: python-3.13' or 'python: 3.13'" in text


# ---------------------------------------------------------------------------
# Structured log — exact field values
# ---------------------------------------------------------------------------


def test_upload_logs_exact_bot_id_and_python_version(client, mock_queue) -> None:
    """bot_id and python_version must be logged with their actual values."""
    from web.utils import DEFAULT_PYTHON_VERSION

    with capture_logs() as cap:
        upload(client, "TypedBot")
    event = next(e for e in cap if e["event"] == "bot_uploaded")
    assert isinstance(event["bot_id"], int)
    assert event["bot_id"] > 0
    assert event["python_version"] == DEFAULT_PYTHON_VERSION


# ---------------------------------------------------------------------------
# Success response — listing and name are forwarded to the template
# ---------------------------------------------------------------------------


def test_success_response_renders_previously_submitted_bots(client) -> None:
    """The success page must include bots already in the DB, proving
    list_for_homepage() was called and passed as the 'bots' context key."""
    upload(client, "Alpha")
    resp = upload(client, "Beta")
    assert resp.status_code == 200
    text = html.unescape(resp.text)
    assert "'Beta' submitted successfully!" in text
    # Alpha must appear in the listing — proves bots.list_for_homepage()
    # was fetched and forwarded (not replaced with None).
    assert "Alpha" in text


# ---------------------------------------------------------------------------
# encode_cookie — safe="" must encode forward slash
# ---------------------------------------------------------------------------


def test_encode_cookie_percent_encodes_forward_slash() -> None:
    """safe='' ensures '/' is percent-encoded; the default safe='/' would
    leave it raw, which can confuse cookie parsers."""
    from web.utils import encode_cookie

    result = encode_cookie({"A/B": "token"})
    assert "/" not in result
    assert "%2F" in result.upper()


# ---------------------------------------------------------------------------
# extract_bot_name — off-by-one in slice (stripped[5:] vs stripped[6:])
# ---------------------------------------------------------------------------


def test_extract_bot_name_no_space_after_colon() -> None:
    """'name:A' (no space) must return 'A', not None.

    With stripped[6:] the result is '' → None; with the correct stripped[5:]
    it's 'A'. This test can't be caught with 'name: A' because the leading
    space is stripped away in both cases."""
    source = '"""\nname:A\n"""\nimport sys\n'
    assert extract_bot_name(source) == "A"


# ---------------------------------------------------------------------------
# extract_python_version — correct slice index
# ---------------------------------------------------------------------------


def test_extract_python_version_no_space_after_colon() -> None:
    """'python:3.12' (no space) must return '3.12', not None.

    stripped[7:] of 'python:3.12' = '3.12'; stripped[8:] = '.12'
    which is not in SUPPORTED_PYTHON_VERSIONS and returns None. The test
    with 'python: 3.12' would give stripped[7:] = ' 3.12' → strip → '3.12'
    and stripped[8:] = '3.12' → same result — can't detect the off-by-one."""
    from web.utils import SUPPORTED_PYTHON_VERSIONS, extract_python_version

    latest = SUPPORTED_PYTHON_VERSIONS[-1]
    source = f'"""\nname: Bot\npython:{latest}\n"""\nimport sys\n'
    assert extract_python_version(source) == latest


# ---------------------------------------------------------------------------
# _success_response — samesite cookie attribute
# ---------------------------------------------------------------------------


def test_success_response_sets_samesite_lax_cookie(client, mock_queue) -> None:
    """The ownership cookie must be set with samesite=lax.
    Dropping samesite would use the browser's default (often 'None' which
    requires Secure) or leave CSRF exposure."""
    resp = upload(client, "SameSiteBot")
    set_cookie = resp.headers.get("set-cookie", "")
    assert "samesite=lax" in set_cookie.lower()


# ---------------------------------------------------------------------------
# web.runtimes — RUNTIMES allowlist
# ---------------------------------------------------------------------------


def test_runtimes_contains_python_310_through_314() -> None:
    from web.runtimes import RUNTIMES

    for v in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        assert f"python-{v}" in RUNTIMES


def test_runtime_has_expected_fields() -> None:
    from web.runtimes import RUNTIMES

    rt = RUNTIMES["python-3.13"]
    assert rt.image == "pyowa/bot-runner-python:3.13"
    assert rt.interpreter == "python"
    assert rt.ext == ".py"


def test_default_runtime_key_is_python_314() -> None:
    from web.runtimes import DEFAULT_RUNTIME_KEY

    assert DEFAULT_RUNTIME_KEY == "python-3.14"


def test_default_runtime_key_is_in_runtimes() -> None:
    from web.runtimes import DEFAULT_RUNTIME_KEY, RUNTIMES

    assert DEFAULT_RUNTIME_KEY in RUNTIMES


# ---------------------------------------------------------------------------
# extract_runtime_key
# ---------------------------------------------------------------------------


def test_extract_runtime_key_language_field() -> None:
    from web.utils import extract_runtime_key

    source = '"""\nname: MyBot\nlanguage: python-3.13\n"""\nimport sys\n'
    assert extract_runtime_key(source) == "python-3.13"


def test_extract_runtime_key_python_alias() -> None:
    from web.utils import extract_runtime_key

    source = '"""\nname: MyBot\npython: 3.13\n"""\nimport sys\n'
    assert extract_runtime_key(source) == "python-3.13"


def test_extract_runtime_key_invalid_language_returns_none() -> None:
    from web.utils import extract_runtime_key

    source = '"""\nname: MyBot\nlanguage: ruby-99\n"""\nimport sys\n'
    assert extract_runtime_key(source) is None


def test_extract_runtime_key_invalid_python_returns_none() -> None:
    from web.utils import extract_runtime_key

    source = '"""\nname: MyBot\npython: 3.9\n"""\nimport sys\n'
    assert extract_runtime_key(source) is None


def test_extract_runtime_key_no_field_returns_default() -> None:
    from web.runtimes import DEFAULT_RUNTIME_KEY
    from web.utils import extract_runtime_key

    source = '"""\nname: MyBot\n"""\nimport sys\n'
    assert extract_runtime_key(source) == DEFAULT_RUNTIME_KEY


def test_extract_runtime_key_language_takes_priority_over_python() -> None:
    from web.utils import extract_runtime_key

    source = '"""\nname: MyBot\nlanguage: python-3.12\npython: 3.13\n"""\nimport sys\n'
    assert extract_runtime_key(source) == "python-3.12"


def test_extract_runtime_key_syntax_error_returns_none() -> None:
    from web.utils import extract_runtime_key

    assert extract_runtime_key("def (:\n") is None


def test_extract_runtime_key_empty_source_returns_none() -> None:
    from web.utils import extract_runtime_key

    assert extract_runtime_key("") is None


def test_extract_runtime_key_no_space_after_colon() -> None:
    from web.utils import extract_runtime_key

    source = '"""\nname: MyBot\nlanguage:python-3.13\n"""\nimport sys\n'
    assert extract_runtime_key(source) == "python-3.13"


# ---------------------------------------------------------------------------
# pick_runtime_key
# ---------------------------------------------------------------------------


def test_pick_runtime_key_higher_python_version_wins() -> None:
    from messaging.routing import pick_runtime_key

    assert pick_runtime_key("python-3.11", "python-3.13") == "python-3.13"
    assert pick_runtime_key("python-3.13", "python-3.11") == "python-3.13"


def test_pick_runtime_key_equal_versions_returns_first() -> None:
    from messaging.routing import pick_runtime_key

    assert pick_runtime_key("python-3.13", "python-3.13") == "python-3.13"


# ---------------------------------------------------------------------------
# Upload flow — runtime_key stored on bot DB row
# ---------------------------------------------------------------------------


async def test_upload_with_language_field_stores_runtime_key(client, engine) -> None:
    source = b'"""\nname: LangBot\nlanguage: python-3.12\n"""\nimport sys\n'
    client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(Bot.runtime_key).where(Bot.versioned_name == "LangBot")
            )
        ).one()
    assert row[0] == "python-3.12"


async def test_upload_with_python_alias_stores_runtime_key(client, engine) -> None:
    source = b'"""\nname: AliasBot\npython: 3.12\n"""\nimport sys\n'
    client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(Bot.runtime_key).where(Bot.versioned_name == "AliasBot")
            )
        ).one()
    assert row[0] == "python-3.12"


def test_upload_with_invalid_language_rejected(client) -> None:
    source = b'"""\nname: BadRuntime\nlanguage: cobol-85\n"""\nimport sys\n'
    resp = client.post("/submit", files={"file": ("bot.py", source, "text/plain")})
    assert "Invalid runtime" in resp.text


# ---------------------------------------------------------------------------
# BuildPodMessage — new web upload behavior (step 6)
# ---------------------------------------------------------------------------


def test_upload_publishes_one_build_pod_message(client, mock_queue) -> None:
    """Uploading a bot publishes exactly one BuildPodMessage to matches.build."""
    upload(client, "BuildBot")
    assert len(mock_queue.build_pod_messages) == 1


def test_upload_build_pod_message_has_correct_runtime_key(client, mock_queue) -> None:
    """The BuildPodMessage runtime_key matches the bot's declared runtime."""
    from web.runtimes import DEFAULT_RUNTIME_KEY

    upload(client, "RuntimeBuildBot")
    assert mock_queue.build_pod_messages[0].runtime_key == DEFAULT_RUNTIME_KEY


async def test_upload_build_pod_message_has_correct_bot_id(
    client, engine, mock_queue
) -> None:
    """The BuildPodMessage bot_id matches the newly-inserted bot DB row."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    upload(client, "BotIdCheckBot")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(Bot.id).where(Bot.versioned_name == "BotIdCheckBot")
            )
        ).one()
    assert mock_queue.build_pod_messages[0].bot_id == row[0]


