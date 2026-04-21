"""SQLAlchemy 2.0 async 엔진 + DeclarativeBase."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from api.core.config import settings


class Base(DeclarativeBase):
    """모든 ORM 모델의 부모."""


engine = create_async_engine(
    settings.database_url,
    echo=(settings.env == "development"),
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
