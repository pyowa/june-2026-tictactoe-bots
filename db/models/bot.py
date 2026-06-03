from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    base_name: Mapped[str]
    versioned_name: Mapped[str] = mapped_column(unique=True)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    owner_token: Mapped[str]
    file_path: Mapped[str]
    python_version: Mapped[str] = mapped_column(default="3", server_default="3")
    submitted_at: Mapped[datetime] = mapped_column(
        server_default=func.datetime("now"),
    )
