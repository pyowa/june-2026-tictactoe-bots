from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    from entities.move.model import Move


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint(
            "result IN ('x_wins', 'o_wins', 'cat', 'x_forfeit', 'o_forfeit')",
            name="ck_matches_result",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bot_x_id: Mapped[int] = mapped_column(ForeignKey("bots.id"))
    bot_o_id: Mapped[int] = mapped_column(ForeignKey("bots.id"))
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"))
    result: Mapped[str]
    played_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(),
    )

    moves: Mapped[list["Move"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
