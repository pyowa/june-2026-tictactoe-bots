"""
Stable contracts for the pod-per-bot pipeline.

Web publishes BuildPodMessage to BUILD_POD_QUEUE; pod_builder creates a pod and
publishes PodReadyMessage to POD_READY_QUEUE; match_scheduler schedules pairings
and publishes MatchOndeck to MATCH_ONDECK_QUEUE; ondeck_handler runs each match.
"""

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Queue name constants
# ---------------------------------------------------------------------------

BUILD_POD_QUEUE = "matches.build"
POD_READY_QUEUE = "matches.schedule"
MATCH_ONDECK_QUEUE = "matches.ondeck"


class BuildPodMessage(BaseModel):
    """Sent by web when a bot is uploaded; tells pod_builder to create a pod."""

    model_config = ConfigDict(frozen=True)

    bot_id: int
    runtime_key: str


class PodReadyMessage(BaseModel):
    """Sent by pod_builder when a pod is ready; triggers match scheduling."""

    model_config = ConfigDict(frozen=True)

    bot_id: int


class MatchOndeck(BaseModel):
    """Sent by match_scheduler for each pairing; tells match_runner to run a match."""

    model_config = ConfigDict(frozen=True)

    bot_x_id: int
    bot_o_id: int
    correlation_id: str
