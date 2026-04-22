"""``/hs/{code}`` 라우터 실 DB 통합.

- 10자리 세번: ``selectinload(HSCode.tariff_rates)`` 조인이 실제 Postgres 에서 동작
- 4자리 호 / 2자리 류: ``func.substr`` + ``group_by`` 경로
- 404 경로 (데이터 없음)
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.db.session import get_db
from api.deps import get_current_user
from api.routers import hs as hs_router

from .conftest import seed_hs_code, seed_tariff_rate, seed_user


@pytest_asyncio.fixture
async def app(db):
    """테스트 앱 + dep override. db 는 테스트 함수 스코프 세션."""
    user = await seed_user(db)

    fastapi_app = FastAPI(title="test-hs")
    fastapi_app.include_router(hs_router.router)

    async def _override_db():
        yield db

    def _override_user():
        return user

    fastapi_app.dependency_overrides[get_db] = _override_db
    fastapi_app.dependency_overrides[get_current_user] = _override_user
    return fastapi_app


@pytest.mark.asyncio
async def test_hs_detail_returns_master_and_rates(app, db) -> None:
    await seed_hs_code(db, hs_code="8471300000", name_kr="휴대용 ADP", name_en="Portable ADP")
    await seed_tariff_rate(
        db, hs_code="8471300000", fta_code="A", tax_rate=8.0, apply_start=date(2024, 1, 1)
    )
    await seed_tariff_rate(
        db, hs_code="8471300000", fta_code="FKR1", tax_rate=0.0, apply_start=date(2024, 1, 1)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/hs/8471300000")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "tariff_line"
    assert body["code"] == "8471300000"
    assert body["chapter_number"] == 84
    assert body["section"]["roman"]  # 제16부 로마 숫자 반환

    detail = body["detail"]
    assert detail["hs_code"] == "8471300000"
    assert detail["name_kr"] == "휴대용 ADP"
    assert detail["name_en"] == "Portable ADP"
    assert {t["fta_code"] for t in detail["tariff_rates"]} == {"A", "FKR1"}


@pytest.mark.asyncio
async def test_hs_detail_404_when_missing(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/hs/0000000000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_hs_heading_returns_subheading_children(app, db) -> None:
    """4자리 호 조회 시 하위 6자리 소호 distinct 목록 반환."""
    await seed_hs_code(db, hs_code="8471300000", name_kr="노트북")
    await seed_hs_code(db, hs_code="8471301000", name_kr="울트라북")
    await seed_hs_code(db, hs_code="8471410000", name_kr="데스크톱")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/hs/8471")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "heading"
    assert body["code"] == "8471"
    assert body["detail"] is None
    codes = [c["code"] for c in body["children"]]
    assert set(codes) == {"847130", "847141"}


@pytest.mark.asyncio
async def test_hs_chapter_returns_heading_children(app, db) -> None:
    """2자리 류 조회 시 하위 4자리 호 distinct 목록 반환."""
    await seed_hs_code(db, hs_code="8471300000")
    await seed_hs_code(db, hs_code="8471410000")
    await seed_hs_code(db, hs_code="8473300000")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/hs/84")

    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "chapter"
    codes = [c["code"] for c in body["children"]]
    assert set(codes) == {"8471", "8473"}


@pytest.mark.asyncio
async def test_hs_requires_auth(db) -> None:
    """override 없이 구성한 앱은 실제 ``get_current_user`` 가 발동 → 쿠키/헤더 없으니 401."""
    plain_app = FastAPI()
    plain_app.include_router(hs_router.router)

    async def _override_db_only():
        yield db

    plain_app.dependency_overrides[get_db] = _override_db_only
    async with AsyncClient(
        transport=ASGITransport(app=plain_app), base_url="http://test"
    ) as client:
        r = await client.get("/hs/8471300000")
    assert r.status_code == 401
