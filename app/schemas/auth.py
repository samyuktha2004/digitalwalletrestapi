import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.security import BCRYPT_MAX_BYTES


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    # bcrypt truncates silently past 72 bytes — reject instead of surprising the user.
    password: str = Field(min_length=8, max_length=BCRYPT_MAX_BYTES)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    created_at: datetime
