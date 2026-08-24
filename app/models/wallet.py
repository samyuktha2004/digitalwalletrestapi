import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, uuid_pk


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


if TYPE_CHECKING:
    from app.models.user import User

MONEY = Numeric(12, 2)


class Wallet(Base):
    __tablename__ = "wallets"
    # Last line of defence: even if the service layer is wrong, the DB refuses
    # to store a negative balance.
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_wallets_balance_non_negative"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"), nullable=False)
    # Python-side, not server_default/func.now(): a SQL-side onupdate leaves the
    # attribute expired after commit, and refreshing it would need sync IO from
    # async code (MissingGreenlet). Client-side value is already known.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="wallet")
