import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.transaction import TransactionStatus, TransactionType

# Positive, at most 2dp, fits Numeric(12, 2). Pydantic enforces this before any
# money code runs, so the service layer never sees a bad amount.
Amount = Field(gt=0, max_digits=12, decimal_places=2)


class AmountIn(BaseModel):
    amount: Decimal = Amount


class TransferIn(BaseModel):
    recipient_username: str = Field(min_length=3, max_length=50)
    amount: Decimal = Amount


class EncryptedPayload(BaseModel):
    payload: str = Field(min_length=1, description="<base64(iv)>:<base64(ciphertext)>")


class WalletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    balance: Decimal
    updated_at: datetime


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: TransactionType
    amount: Decimal
    recipient_wallet_id: uuid.UUID | None
    status: TransactionStatus
    created_at: datetime
