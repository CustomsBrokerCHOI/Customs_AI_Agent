"""Alembic 마이그레이션 환경 (async SQLAlchemy 2.0).

설정 주입:
- ``api.core.config.settings.database_url`` 을 런타임 URL 로 사용
- ``api.db.models`` 를 import 하여 모든 ORM 모델을 메타데이터에 등록

실행::

    cd api
    alembic upgrade head           # 최신 스키마 적용
    alembic revision --autogenerate -m "메시지"   # 모델 변경 후 마이그레이션 생성
    alembic downgrade -1           # 한 단계 되돌리기
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 모델 등록 (side-effect import — 메타데이터에 테이블 붙음)
from api.db import models  # noqa: F401
from api.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """런타임에 api.core.config 에서 DATABASE_URL 가져오기."""
    from api.core.config import settings

    return settings.database_url


def run_migrations_offline() -> None:
    """오프라인(SQL 덤프) 모드."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
