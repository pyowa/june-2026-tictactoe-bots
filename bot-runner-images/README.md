# bot-runner-images

Per-Python-version container images used by the dispatcher to run bot code during a match.

## How it works

The dispatcher creates one pod per bot at the start of each match. Each pod runs `turn_server.py` — a minimal stdlib HTTP server (no extra dependencies). The bot's source code is passed in at pod creation time via the `SOURCE_B64` environment variable (base64-encoded Python source).

During the match the dispatcher sends one `POST /turn` request per move:

```
POST /turn
{"symbol": "X", "board": "XO......."}
→ {"board": "XO..X...."}   # success
→ {"error": "timeout after 10s"}  # failure
```

The pod also exposes `GET /health` on port 8080 for the Kubernetes readiness probe. Both pods are deleted in a `finally` block in `dispatcher/pods.py` after the match concludes.

## Building

```bash
docker build -t pyowa/bot-runner-python:3.12 \
  --build-arg PY_VERSION=3.12 \
  bot-runner-images/python/
```

Supported versions match the Python versions declared in `dispatcher/pods.py`. The image tag must match the version string stored in `bots.python_version` — the dispatcher uses that value to select which pod image to create.

## Directory structure

```
bot-runner-images/
└── python/
    ├── Dockerfile       # ARG PY_VERSION; FROM python:${PY_VERSION}-slim
    └── turn_server.py   # stdlib HTTP server, no extra deps
```

One `python/` subdirectory exists today. If a second language runtime is ever added, it would get its own sibling directory here.
