"""
Acceptance placeholder — the old per-turn RPC architecture (MatchRequest /
MatchReply on match.requests) has been replaced by the pod-per-bot pipeline.
New acceptance coverage lives in the pod_builder / ondeck_handler tests.
"""

import pytest

pytestmark = pytest.mark.acceptance


# TODO smell
@pytest.mark.skip(
    reason="Old RPC architecture removed; new pipeline acceptance tests TBD"
)
def test_match_rpc_roundtrip() -> None:
    pass
