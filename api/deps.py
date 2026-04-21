"""FastAPI 공용 의존성 — 인증·권한."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.security import SecurityError, decode_token
from api.db.models import User
from api.db.session import get_db

# 쿠키 이름 (auth 라우터에서 set_cookie 할 때 같은 이름 사용)
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    access_token: str | None = Cookie(None, alias=ACCESS_COOKIE),
    authorization: str | None = Header(None),
) -> User:
    """현재 관세사 반환. 쿠키 우선, Authorization 헤더 fallback.

    헤더 형식: ``Authorization: Bearer <token>`` (API 클라이언트·모바일용).
    """
    token = access_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 필요",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token, expected_type="access")
    except SecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰 sub 불량",
        ) from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자 없음 또는 비활성",
        )
    return user


# Type alias for cleaner signatures in routers
CurrentUser = Annotated[User, Depends(get_current_user)]
