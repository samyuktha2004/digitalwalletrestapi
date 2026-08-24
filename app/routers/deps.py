import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import DecryptionError, decrypt
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models import User
from app.schemas import EncryptedPayload, TransferIn

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep
) -> User:
    subject = decode_access_token(token)
    if subject is None:
        raise _CREDENTIALS_ERROR
    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        raise _CREDENTIALS_ERROR from None
    user = await session.get(User, user_id)
    if user is None:
        raise _CREDENTIALS_ERROR
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def decrypted_transfer(body: EncryptedPayload) -> TransferIn:
    """Body is {"payload": "<iv>:<ciphertext>"}; hand the route a real TransferIn.

    A dependency rather than an ASGI middleware on purpose: middleware would
    have to buffer and rewrite the request body for one route, and could not
    reuse FastAPI's validation or show the real schema in /docs.
    """
    try:
        plaintext = decrypt(body.payload, settings.aes_key)
    except DecryptionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid payload: {exc}") from exc
    try:
        return TransferIn.model_validate_json(plaintext)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Decrypted payload invalid: {exc.errors()}"
        ) from exc


DecryptedTransfer = Annotated[TransferIn, Depends(decrypted_transfer)]
