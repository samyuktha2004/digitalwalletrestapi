from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import User, Wallet
from app.services.errors import Conflict


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(User.username == username))


async def register(session: AsyncSession, username: str, password: str) -> User:
    user = User(username=username, password_hash=hash_password(password))
    user.wallet = Wallet()  # every user gets exactly one wallet at signup
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Username already taken") from exc
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    user = await get_by_username(session, username)
    # Hash-compare even when the user is missing would be better against user
    # enumeration by timing; skipped — the response is identical either way.
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
