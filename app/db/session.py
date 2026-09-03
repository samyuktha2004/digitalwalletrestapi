import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Serverless (Vercel) runs many short-lived function instances, each of which
# would hold its own idle pool and exhaust the database's connection limit.
# NullPool opens a connection per request and closes it after. Concurrency is
# unaffected -- every request still gets its own connection, which is what
# SELECT ... FOR UPDATE needs to contend over.
_serverless = bool(os.getenv("VERCEL"))

engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool if _serverless else None,
    pool_pre_ping=not _serverless,
    connect_args={
        # A wallet that waits forever on a row lock is a hung API worker. Fail
        # the request instead; the client can retry.
        "server_settings": {"lock_timeout": "5000"},
        # Supabase's pooler multiplexes connections, so a prepared statement
        # cached under one backend can be replayed against another and fail
        # ("prepared statement does not exist"). Disable asyncpg's cache there.
        **({"statement_cache_size": 0} if _serverless else {}),
    },
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
