from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    base_name: Mapped[str]
    versioned_name: Mapped[str] = mapped_column(unique=True)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    owner_token: Mapped[str]
    # Bot source code as bytes. Deferred — not loaded by default `select(Bot)`
    # queries, because most reads (leaderboards, listings, filtering) don't
    # need the BYTEA payload. Touch `bot.source` on a loaded entity to lazy-
    # load it, or use `.options(undefer(Bot.source))` / explicit column
    # `select(Bot.id, Bot.source)` to opt in eagerly. Nullable today to keep
    # migrations painless; tighten to NOT NULL once the polling runner is
    # retired and only the event-driven workers (which require this) consume it.
    source: Mapped[bytes | None] = mapped_column(deferred=True)
    python_version: Mapped[str] = mapped_column(default="3", server_default="3")
    runtime_key: Mapped[str] = mapped_column(
        default="python-3.14", server_default="'python-3.14'"
    )
    pod_name: Mapped[str | None] = mapped_column(default=None)
    pod_ready: Mapped[bool] = mapped_column(default=False, server_default="false")
    submitted_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(),
    )
