# syntax=docker/dockerfile:1.7

# Single Dockerfile with build targets: web, dispatcher, worker, test-runner.
# All share the `base` stage which installs the full project
# dependencies via uv. Source code is bind-mounted at runtime from compose
# for dev convenience, so the image does not COPY the project source.
#
# PY_VERSION is a build arg so the multi-Python worker fleet (next step in
# TODO.md) can build the same image against different Python versions just
# by changing the arg.
ARG PY_VERSION=3.13

# ---------------------------------------------------------------------------
# base: install OS deps + uv + project deps. Shared by all three targets.
# ---------------------------------------------------------------------------
FROM python:${PY_VERSION}-slim AS base

# curl is used by the web container's healthcheck. Keep this tight.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv from its official slim image (avoids pulling pipx/pip).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Install deps only (no project source — that's bind-mounted at runtime).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# web: FastAPI app served by uvicorn. --reload picks up bind-mounted source.
# ---------------------------------------------------------------------------
FROM base AS web
EXPOSE 8000
CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
# worker: consumes turn.requests, runs the bot subprocess.
# ---------------------------------------------------------------------------
FROM base AS worker
CMD ["python", "-m", "runner.turn_worker"]

# ---------------------------------------------------------------------------
# dispatcher: runs inside the kind/AKS cluster; consumes turn.requests and
# creates per-turn Kubernetes Jobs. Needs the kubernetes client (dispatcher
# dependency group) plus its source dependencies baked in (no bind mount).
# ---------------------------------------------------------------------------
FROM base AS dispatcher
RUN uv sync --frozen --no-dev --group dispatcher --no-install-project
# Bake in the source modules the dispatcher depends on (no compose bind mount).
COPY dispatcher/ ./dispatcher/
COPY messaging/ ./messaging/
COPY db/ ./db/
COPY entities/ ./entities/
COPY runner/__init__.py ./runner/__init__.py
COPY runner/engine.py ./runner/engine.py
COPY web/__init__.py ./web/__init__.py
COPY web/runtimes.py ./web/runtimes.py
CMD ["python", "-m", "dispatcher.main"]

# ---------------------------------------------------------------------------
# match-scheduler: consumes matches.schedule, publishes MatchOndeck to matches.ondeck.
FROM base AS match-scheduler
CMD ["python", "-m", "match_scheduler.main"]

# ---------------------------------------------------------------------------
# test-runner: like base but with dev deps (pytest, mutmut, etc.). Used by
# the `mutmut` compose service so mutation runs happen on Linux where fork
# is safe (mutmut v3 hardcodes os.fork(), which crashes on macOS + py3.14).
# ---------------------------------------------------------------------------
FROM base AS test-runner
RUN uv sync --frozen --no-install-project
