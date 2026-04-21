"""DB 세션 FastAPI 의존성."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import SessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """요청당 하나의 AsyncSession 제공."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
