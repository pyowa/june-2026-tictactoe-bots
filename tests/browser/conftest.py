"""Browser-test fixtures.

These tests run in a dedicated pytest invocation (`make browser-test`) so
their session-scoped uvicorn + Playwright fixtures don't bleed event-loop
state into the asyncio-driven unit tests.
"""

import json
import os
import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page

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


@pytest.fixture(autouse=True)
def _collect_js_coverage(
    page: Page, context: Any, request: pytest.FixtureRequest
) -> Iterator[None]:
    """Opt-in JS coverage collection for browser tests.

    Activates only when `JS_COVERAGE_DIR` is set in the environment, so the
    default `make browser-test` run pays no overhead. The Make target
    `js-browser-coverage` sets it, then runs `scripts/js_coverage_report.py`
    on the collected JSON to produce a per-file line report.

    Playwright Python doesn't expose the high-level `page.coverage` API, so
    we drive the Chrome DevTools Protocol directly to start precise V8
    profiling, take the coverage snapshot, and pair each scriptId with its
    source text (Debugger.getScriptSource)."""
    out_dir = os.environ.get("JS_COVERAGE_DIR")
    if not out_dir:
        yield
        return

    session = context.new_cdp_session(page)
    session.send("Profiler.enable")
    session.send("Debugger.enable")
    session.send(
        "Profiler.startPreciseCoverage",
        {"callCount": True, "detailed": True},
    )

    try:
        yield
    finally:
        result = session.send("Profiler.takePreciseCoverage")
        for entry in result.get("result", []):
            script_id = entry.get("scriptId")
            if not script_id:
                continue
            try:
                src = session.send(
                    "Debugger.getScriptSource", {"scriptId": script_id}
                )
                entry["source"] = src.get("scriptSource", "")
            except Exception:
                pass
        session.send("Profiler.stopPreciseCoverage")
        session.detach()

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[:200]
        Path(out_dir, f"{safe_name}.json").write_text(
            json.dumps(result.get("result", []))
        )
