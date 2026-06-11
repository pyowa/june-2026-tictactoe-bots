import pytest
from pydantic import ValidationError

from messaging.contracts import TURN_REQUEST_QUEUE, TurnReply, TurnRequest


def test_turn_request_queue_name_is_stable() -> None:
    # Wire contract — bots, workers, dispatcher all rely on this exact string.
    # Changing it is a breaking change for every running consumer.
    assert TURN_REQUEST_QUEUE == "turn.requests"


def test_turn_request_field_set() -> None:
    assert set(TurnRequest.model_fields.keys()) == {
        "symbol",
        "board",
        "source_b64",
        "runtime_key",
    }


def test_turn_request_round_trips_through_json() -> None:
    req = TurnRequest(
        symbol="X",
        board=".|.|.\n.|.|.\n.|.|.",
        source_b64="Zm9v",
        runtime_key="python-3.13",
    )
    assert TurnRequest.model_validate_json(req.model_dump_json()) == req


def test_turn_request_rejects_invalid_symbol() -> None:
    with pytest.raises(ValidationError):
        TurnRequest.model_validate(
            {
                "symbol": "Z",
                "board": ".|.|.",
                "source_b64": "",
                "runtime_key": "python-3.13",
            }
        )


def test_turn_request_rejects_missing_field() -> None:
    with pytest.raises(ValidationError):
        TurnRequest.model_validate(
            {
                "symbol": "X",
                "board": ".|.|.",
                "source_b64": "",
                # runtime_key missing
            }
        )


def test_turn_reply_round_trips_through_json() -> None:
    ok = TurnReply(board="X|.|.\n.|.|.\n.|.|.")
    err = TurnReply(error="timeout after 5s")
    assert TurnReply.model_validate_json(ok.model_dump_json()) == ok
    assert TurnReply.model_validate_json(err.model_dump_json()) == err
    assert ok.error is None
    assert err.board is None
