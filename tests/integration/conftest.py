"""Phase 5-A 통합 테스트용 공용 픽스처.

접근
----
- **mini 테스트 앱**: `api.main.app` 은 `api.routers.auth` 를 통해 `EmailStr` 를
  참조하므로 ``email-validator`` 가 설치된 환경에서만 로드 가능. 환경 의존을
  줄이기 위해 필요한 라우터(classify/hs/health) 만 포함하는 test-only ``FastAPI``
  인스턴스를 빌드한다. 인증은 ``get_current_user`` dep override 로 치환.
- **Fake AsyncSession**: DB 접근은 사용자 제공 fixture 에서 필요한 부분만 모킹.
  기본 fixture 는 모든 메서드가 ``AsyncMock`` 인 세션을 만들어 주고, 개별 테스트가
  반환값을 설정한다.
- **Rate limit/Audit**: 서비스 함수 자체는 모킹 가능. 기본은 한도 통과.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.db.models import User
from api.db.session import get_db
from api.deps import get_current_user
from api.routers import classify as classify_router
from api.routers import health as health_router
from api.routers import hs as hs_router


# ---- Fake session factory ----


def make_fake_db() -> MagicMock:
    """AsyncSession 시늉 — execute/get/add/flush/commit/refresh/rollback 모두 awaitable."""
    db = MagicMock(name="AsyncSession")
    db.execute = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    return db


def make_fake_user(**overrides: Any) -> User:
    """세션에 attach 없이 필드만 채운 ``User`` 인스턴스 — dep override 에서 반환."""
    u = User(
        email=overrides.get("email", "broker@example.com"),
        name=overrides.get("name", "관세사 테스터"),
        password_hash="$2b$12$fake",
        role=overrides.get("role", "broker"),
        is_active=overrides.get("is_active", True),
    )
    # __init__ 이 uuid default 를 채우지만 테스트에서 고정 id 선호
    u.id = overrides.get("id", uuid.uuid4())
    u.created_at = overrides.get("created_at", datetime.now(timezone.utc))
    return u


# ---- 테스트용 앱 빌더 ----


def build_test_app(
    *,
    db: Any,
    user: User | None,
) -> FastAPI:
    """classify/hs/health 라우터만 포함한 mini app. auth dep 은 override."""
    app = FastAPI(title="test-app")
    app.include_router(health_router.router)
    app.include_router(classify_router.router)
    app.include_router(hs_router.router)

    async def _override_db():
        yield db

    def _override_user():
        if user is None:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="테스트: 비인증")
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    return app


# ---- 공용 픽스처 ----


@pytest.fixture
def fake_db() -> MagicMock:
    return make_fake_db()


@pytest.fixture
def fake_user() -> User:
    return make_fake_user()


@pytest.fixture
def authed_client(fake_db: MagicMock, fake_user: User):
    """인증된 사용자 컨텍스트. TestClient 반환."""
    app = build_test_app(db=fake_db, user=fake_user)
    with TestClient(app) as client:
        yield client, fake_db, fake_user


@pytest.fixture
def unauthed_client(fake_db: MagicMock):
    """비인증 컨텍스트 — ``CurrentUser`` 가 401 을 내도록."""
    app = build_test_app(db=fake_db, user=None)
    with TestClient(app) as client:
        yield client, fake_db
