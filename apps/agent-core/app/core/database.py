from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def to_async_database_url(database_url: str) -> str:
    """Use one PostgreSQL URL for Prisma while selecting asyncpg in Python."""
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        to_async_database_url(database_url),
        pool_pre_ping=True,
    )
