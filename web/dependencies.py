from fastapi import Request

from messaging.queue import Queue


def get_queue(request: Request) -> Queue:
    """FastAPI dependency: return the process-wide queue created by the
    `lifespan` in `web/main.py` and stashed on `app.state`.

    Tests substitute a fake by setting
    `app.dependency_overrides[get_queue] = lambda: fake`."""
    return request.app.state.queue
