import pytest
from pydantic import ValidationError  # noqa: F401 (used in raises checks)

from messaging.contracts import (
    BUILD_POD_QUEUE,
    MATCH_ONDECK_QUEUE,
    POD_READY_QUEUE,
    BuildPodMessage,
    MatchOndeck,
    PodReadyMessage,
)

# ---------------------------------------------------------------------------
# New pipeline queue name constants
# ---------------------------------------------------------------------------


def test_build_pod_queue_name_is_stable() -> None:
    assert BUILD_POD_QUEUE == "matches.build"


def test_pod_ready_queue_name_is_stable() -> None:
    assert POD_READY_QUEUE == "matches.schedule"


def test_match_ondeck_queue_name_is_stable() -> None:
    assert MATCH_ONDECK_QUEUE == "matches.ondeck"


# ---------------------------------------------------------------------------
# BuildPodMessage
# ---------------------------------------------------------------------------


def test_build_pod_message_field_set() -> None:
    assert set(BuildPodMessage.model_fields.keys()) == {"bot_id", "runtime_key"}


def test_build_pod_message_round_trips_through_json() -> None:
    msg = BuildPodMessage(bot_id=42, runtime_key="python-3.12")
    assert BuildPodMessage.model_validate_json(msg.model_dump_json()) == msg


def test_build_pod_message_rejects_missing_bot_id() -> None:
    with pytest.raises(ValidationError):
        BuildPodMessage.model_validate({"runtime_key": "python-3.12"})


def test_build_pod_message_rejects_missing_runtime_key() -> None:
    with pytest.raises(ValidationError):
        BuildPodMessage.model_validate({"bot_id": 1})


# ---------------------------------------------------------------------------
# PodReadyMessage
# ---------------------------------------------------------------------------


def test_pod_ready_message_field_set() -> None:
    assert set(PodReadyMessage.model_fields.keys()) == {"bot_id"}


def test_pod_ready_message_round_trips_through_json() -> None:
    msg = PodReadyMessage(bot_id=7)
    assert PodReadyMessage.model_validate_json(msg.model_dump_json()) == msg


def test_pod_ready_message_rejects_missing_bot_id() -> None:
    with pytest.raises(ValidationError):
        PodReadyMessage.model_validate({})


# ---------------------------------------------------------------------------
# MatchOndeck
# ---------------------------------------------------------------------------


def test_match_ondeck_field_set() -> None:
    assert set(MatchOndeck.model_fields.keys()) == {
        "bot_x_id",
        "bot_o_id",
        "correlation_id",
    }


def test_match_ondeck_round_trips_through_json() -> None:
    msg = MatchOndeck(bot_x_id=1, bot_o_id=2, correlation_id="cid-abc")
    assert MatchOndeck.model_validate_json(msg.model_dump_json()) == msg


def test_match_ondeck_rejects_missing_bot_x_id() -> None:
    with pytest.raises(ValidationError):
        MatchOndeck.model_validate({"bot_o_id": 2, "correlation_id": "cid"})


def test_match_ondeck_rejects_missing_bot_o_id() -> None:
    with pytest.raises(ValidationError):
        MatchOndeck.model_validate({"bot_x_id": 1, "correlation_id": "cid"})


def test_match_ondeck_rejects_missing_correlation_id() -> None:
    with pytest.raises(ValidationError):
        MatchOndeck.model_validate({"bot_x_id": 1, "bot_o_id": 2})
