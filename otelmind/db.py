"""Database connection management.

This module provides two connection mechanisms:
1. asyncpg pool — for high-performance async operations (collector, API)
2. SQLAlchemy URL — for Alembic migrations (sync) and ORM queries

The asyncpg pool is used by the collector's BatchWriter for direct SQL.
The SQLAlchemy async engine is used by the API and watchdog for ORM queries.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from otelmind.config import settings

load_dotenv()


def get_database_url(async_driver: bool = False) -> str:
    """Build the PostgreSQL connection URL from environment variables.

    Args:
        async_driver: If True, use asyncpg driver. If False, use psycopg2 (sync).

    Returns:
        PostgreSQL connection URL string.
    """
    host = settings.db.host
    port = settings.db.port
    db = settings.db.database
    user = settings.db.user
    password = settings.db.password

    if async_driver:
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def create_pool():
    """Create an asyncpg connection pool.

    A connection pool pre-creates a set of database connections and reuses them.
    This avoids the overhead of creating a new TCP connection for every query.
    """
    import asyncpg

    return await asyncpg.create_pool(
        host=settings.db.host,
        port=settings.db.port,
        database=settings.db.database,
        user=settings.db.user,
        password=settings.db.password,
        min_size=5,
        max_size=20,
    )


# SQLAlchemy async engine for ORM operations
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    echo=False,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all OtelMind models."""

    pass


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session scope."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
