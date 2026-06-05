from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from entities.match.model import Match


class Move(Base):
    __tablename__ = "moves"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    move_number: Mapped[int]
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"))
    board_state: Mapped[str]
    error: Mapped[str | None]

    match: Mapped[Match] = relationship(back_populates="moves")
