from types import SimpleNamespace

from web.dependencies import get_queue


def test_get_queue_reads_from_app_state() -> None:
    """The dependency is just a thin reader over `request.app.state.queue`;
    every other test overrides it via `app.dependency_overrides`, so verify
    the real path here with a fake Request."""
    sentinel = object()
    state = SimpleNamespace(queue=sentinel)
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    assert get_queue(request) is sentinel  # type: ignore[arg-type]
