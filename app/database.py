"""
Database configuration for Roan Arbitrage Machine.
Provides async SQLAlchemy engine and session factory.
"""

import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

Base = declarative_base()

_DATABASE_URL = os.getenv("DATABASE_URL", "")

# Convert postgresql:// or postgres:// to postgresql+asyncpg:// for async driver
if _DATABASE_URL.startswith("postgresql://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

if not _DATABASE_URL:
    logger.warning(
        "DATABASE_URL is not set — database features will be unavailable. "
        "Set DATABASE_URL to a PostgreSQL connection string."
    )
    # Use a placeholder so the module can be imported; actual DB calls will fail gracefully
    _DATABASE_URL = "postgresql+asyncpg://localhost/placeholder"

engine = create_async_engine(_DATABASE_URL, echo=False, future=True)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db():
    """FastAPI dependency: yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
