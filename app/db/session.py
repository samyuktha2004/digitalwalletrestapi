from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    # A wallet that waits forever on a row lock is a hung API worker. Fail the
    # request instead; the client can retry.
    connect_args={"server_settings": {"lock_timeout": "5000"}},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session (and therefore one DB connection) per request.

    Required for the locking tests: two concurrent requests must sit on two
    connections, or SELECT ... FOR UPDATE has nothing to contend over.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
