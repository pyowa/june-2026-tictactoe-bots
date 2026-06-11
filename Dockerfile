# syntax=docker/dockerfile:1.7

# Single Dockerfile with three build targets (web, orchestrator, worker).
# All three share the `base` stage which installs the full project
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
# orchestrator: consumes matches.todo, drives the per-turn RPC loop.
# ---------------------------------------------------------------------------
FROM base AS orchestrator
CMD ["python", "-m", "runner.orchestrator"]

# ---------------------------------------------------------------------------
# worker: consumes turn.pyX.Y.requests, runs the bot subprocess.
# ---------------------------------------------------------------------------
FROM base AS worker
CMD ["python", "-m", "runner.turn_worker"]

# ---------------------------------------------------------------------------
# test-runner: like base but with dev deps (pytest, mutmut, etc.). Used by
# the `mutmut` compose service so mutation runs happen on Linux where fork
# is safe (mutmut v3 hardcodes os.fork(), which crashes on macOS + py3.14).
# ---------------------------------------------------------------------------
FROM base AS test-runner
RUN uv sync --frozen --no-install-project
