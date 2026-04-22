"""실 Postgres+pgvector 통합 테스트 공용 픽스처.

## 실행 (로컬)

```bash
docker compose -f docker-compose.test.yml up -d postgres-test
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/customs_test
python -m pytest tests/integration_db/ -v
```

`TEST_DATABASE_URL` 미설정 시 기본값(위와 동일) 사용. DB 연결이 실패하면 모듈 전체
skip — Postgres 가 없는 로컬에서도 다른 테스트 진행을 막지 않는다.

## 설계

- **세션 스코프 engine** + `NullPool`: 이벤트 루프 스코프 불일치(asyncpg pool 이
  원 루프에 바인딩되는 이슈)를 피한다. 함수 스코프 테스트 루프에서도 안전.
- **스키마 생성**: `Base.metadata.create_all` + `CREATE EXTENSION vector` +
  HNSW 인덱스 수동 생성. Alembic 전체 경로는 과도 (테스트 DB 는 매번 drop/create).
- **트랜잭션 격리**: 테스트별로 outer connection 에서 begin → 테스트 끝에 rollback.
  테스트는 `await db.flush()` 로 데이터를 가시화 (commit 아님). `db.run_sync(fn)` 도
  같은 트랜잭션을 공유.
- **Deterministic 임베딩**: `_seed_embedding(key)` 가 sha256→random.Random 시드로
  1536 차원 unit vector. 같은 키는 같은 벡터. 쿼리 벡터와 완전 일치하는 청크를 만들어
  cosine 거리 ~0 을 예측 가능하게 한다.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import uuid
from collections.abc import AsyncGenerator
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from api.db import models as m
from api.db.base import Base

DEFAULT_TEST_URL = "postgresql+asyncpg://postgres:postgres@localhost:5433/customs_test"


def _resolve_test_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)


async def _probe(url: str) -> str | None:
    """DB 연결 + pgvector 확장 사용 가능 여부. 실패 시 skip 메시지 반환."""
    try:
        eng = create_async_engine(url, poolclass=NullPool)
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await eng.dispose()
    except Exception as exc:  # noqa: BLE001
        return f"DB 연결 실패 ({url}): {exc}"
    return None


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _resolve_test_url()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(test_database_url: str) -> AsyncGenerator[AsyncEngine, None]:
    """세션 스코프 engine. pgvector 확장 + 스키마 + HNSW 인덱스 준비.

    DB 연결이 안 되면 모듈 전체 skip.
    """
    err = await _probe(test_database_url)
    if err:
        pytest.skip(err, allow_module_level=True)

    eng = create_async_engine(test_database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # 이전 실행 잔재 제거 후 새로 만들기
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # HNSW 인덱스 (models.py 가 직접 생성하지 않으므로 수동)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_note_chunks_embedding_hnsw "
                "ON note_chunks USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_classification_cases_embedding_hnsw "
                "ON classification_cases USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
        )
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def db(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """함수 스코프 AsyncSession — outer transaction rollback 으로 격리.

    테스트는 ``await db.flush()`` 로 데이터를 가시화한다. ``commit()`` 은 사용 가능
    하지만 (SQLAlchemy 가 savepoint 로 처리), 혼동을 피하기 위해 flush 권장.
    """
    conn: AsyncConnection = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()


# ---- Deterministic 임베딩 ----


def _seed_embedding(key: str, dim: int = 1536) -> list[float]:
    """sha256 시드로 생성한 1536 차원 unit vector. 같은 key → 같은 벡터.

    cosine 거리가 예측 가능하므로 "이 청크가 쿼리와 가장 가까워야 한다" 류의 검증에
    사용. 실제 임베딩 모델은 호출하지 않는다.
    """
    h = hashlib.sha256(key.encode("utf-8")).digest()
    seed = int.from_bytes(h[:8], "little")
    rng = random.Random(seed)
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# ---- Seed helpers ----


async def seed_user(
    db: AsyncSession,
    *,
    email: str | None = None,
    role: str = "broker",
) -> m.User:
    u = m.User(
        id=uuid.uuid4(),
        email=email or f"broker-{uuid.uuid4().hex[:8]}@example.com",
        name="관세사",
        password_hash="$2b$12$fake",
        role=role,
        is_active=True,
    )
    db.add(u)
    await db.flush()
    return u


async def seed_hs_code(
    db: AsyncSession,
    *,
    hs_code: str,
    name_kr: str | None = None,
    name_en: str | None = None,
) -> m.HSCode:
    hs = m.HSCode(
        hs_code=hs_code,
        heading=hs_code[:4],
        sub_heading=hs_code[4:6],
        tariff_line=hs_code[6:10],
        name_kr=name_kr or f"테스트 품명 {hs_code}",
        name_en=name_en or f"Test name {hs_code}",
    )
    db.add(hs)
    await db.flush()
    return hs


async def seed_tariff_rate(
    db: AsyncSession,
    *,
    hs_code: str,
    fta_code: str = "A",
    fta_name: str | None = None,
    tax_rate: float = 8.0,
    apply_start: date | None = None,
) -> m.TariffRate:
    tr = m.TariffRate(
        id=uuid.uuid4(),
        hs_code=hs_code,
        fta_code=fta_code,
        fta_name=fta_name or ("기본세율" if fta_code == "A" else fta_code),
        tax_rate=tax_rate,
        apply_start=apply_start or date(2024, 1, 1),
    )
    db.add(tr)
    await db.flush()
    return tr


async def seed_note_with_chunks(
    db: AsyncSession,
    *,
    heading: str,
    kind: str = "heading_note",
    lang: str = "ko",
    hsk_year: int = 2022,
    content: str = "테스트 해설서 원문",
    chunk_texts: list[str] | None = None,
    embedding_seed: str | None = None,
) -> m.ExplanatoryNote:
    """해설서 + 청크 생성. 청크 임베딩은 ``{embedding_seed or heading}:{i}`` 시드로
    deterministic 생성. 쿼리 벡터를 같은 키로 만들면 정확히 일치.
    """
    note = m.ExplanatoryNote(
        id=uuid.uuid4(),
        heading=heading,
        kind=kind,
        lang=lang,
        hsk_year=hsk_year,
        content=content,
    )
    db.add(note)
    await db.flush()

    texts = chunk_texts if chunk_texts is not None else [content[:500] or content]
    seed_base = embedding_seed or f"{heading}-{kind}-{lang}-{hsk_year}"
    for i, t in enumerate(texts):
        db.add(
            m.NoteChunk(
                id=uuid.uuid4(),
                note_id=note.id,
                chunk_index=i,
                text=t,
                embedding=_seed_embedding(f"{seed_base}:{i}"),
                embedding_model="test-fixture",
            )
        )
    await db.flush()
    return note


async def seed_case(
    db: AsyncSession,
    *,
    case_ref: str,
    hs_code: str | None,
    product_name: str = "테스트 사례",
    description: str | None = None,
    embedding_seed: str | None = None,
) -> m.ClassificationCase:
    c = m.ClassificationCase(
        id=uuid.uuid4(),
        case_ref=case_ref,
        hs_code=hs_code,
        product_name=product_name,
        description=description,
        embedding=_seed_embedding(embedding_seed or case_ref),
        embedding_model="test-fixture",
    )
    db.add(c)
    await db.flush()
    return c
