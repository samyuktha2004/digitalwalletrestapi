import os
import uuid
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

# Supabase's transaction pooler multiplexes many clients onto a few Postgres
# backends, so asyncpg's default sequentially-numbered prepared statement names
# collide across requests -- DuplicatePreparedStatementError, seen in production
# on two concurrent withdrawals. The documented fix is all three together:
# don't cache statements, give every one a unique name, and never hold a pooled
# connection between requests (NullPool, above).
# All three are consumed by SQLAlchemy's asyncpg DBAPI shim out of connect_args
# (see AsyncAdapt_asyncpg_dbapi.connect), not as create_engine() kwargs.
_pooler_connect_args = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
}

engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool if _serverless else None,
    pool_pre_ping=not _serverless,
    connect_args={
        # A wallet that waits forever on a row lock is a hung API worker. Fail
        # the request instead; the client can retry.
        "server_settings": {"lock_timeout": "5000"},
        **(_pooler_connect_args if _serverless else {}),
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
