"""Unit tests for messaging/amqp.py — AMQP message parsing helper."""

from messaging.amqp import parse_amqp_message
from messaging.contracts import BuildPodMessage


def test_parse_amqp_message_success_returns_model() -> None:
    msg = BuildPodMessage(bot_id=7, runtime_key="python-3.14")
    body = msg.model_dump_json().encode()
    result = parse_amqp_message(body, BuildPodMessage)
    assert result is not None
    assert result.bot_id == 7
    assert result.runtime_key == "python-3.14"


def test_parse_amqp_message_invalid_json_returns_none() -> None:
    result = parse_amqp_message(b"not valid json", BuildPodMessage)
    assert result is None


def test_parse_amqp_message_wrong_schema_returns_none() -> None:
    # Valid JSON but missing required fields for the model.
    result = parse_amqp_message(b'{"unexpected": "field"}', BuildPodMessage)
    assert result is None


def test_parse_amqp_message_empty_bytes_returns_none() -> None:
    result = parse_amqp_message(b"", BuildPodMessage)
    assert result is None
