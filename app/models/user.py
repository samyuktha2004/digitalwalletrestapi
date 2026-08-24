import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_col, uuid_pk

if TYPE_CHECKING:
    from app.models.wallet import Wallet


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    # Deliberately lazy: nothing reads current_user.wallet (routes go through
    # wallet_service), and eager-loading it would seed the identity map with a
    # pre-lock copy of the row.
    wallet: Mapped["Wallet"] = relationship(back_populates="user", lazy="raise_on_sql")
