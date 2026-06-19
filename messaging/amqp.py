"""
AMQP message parsing helpers shared across dispatcher consumers.
"""

from typing import TypeVar

import structlog
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_log = structlog.get_logger()


def parse_amqp_message(body: bytes, model: type[T]) -> T | None:
    """Deserialise *body* into *model* using Pydantic's ``model_validate_json``.

    Returns the parsed model on success, or ``None`` if the body is invalid
    JSON or does not match the expected schema. Callers should return early
    when ``None`` is returned.
    """
    try:
        return model.model_validate_json(body)
    except Exception:  # noqa: BLE001 — any parse failure is a protocol error
        _log.error("amqp_message_invalid_json", model=model.__name__)
        return None
