import json
import urllib.parse

from fastapi.testclient import TestClient

import web.main
from tests.conftest import upload


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
