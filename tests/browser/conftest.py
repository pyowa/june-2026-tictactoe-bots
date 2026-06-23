"""Browser-test fixtures.

These tests run in a dedicated pytest invocation (`make browser-test`) so
their session-scoped uvicorn + Playwright fixtures don't bleed event-loop
state into the asyncio-driven unit tests.
"""

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

import db.session
import web.main
from tests.conftest import TEST_ASYNC_URL


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server() -> Iterator[str]:
    """Start uvicorn on a random port in a background thread; yield the URL."""
    db.session.reconfigure(TEST_ASYNC_URL)
    port = _find_free_port()
    config = uvicorn.Config(
        web.main.app, host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn live_server did not start within 10s")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    """pytest-playwright hook: force the full Chromium binary instead of the
    `chrome-headless-shell` variant, which nix's `playwright-driver` doesn't
    ship. The full Chromium runs headless just fine with this channel."""
    return {**browser_type_launch_args, "channel": "chromium"}
