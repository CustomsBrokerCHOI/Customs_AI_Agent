"""인증 엔드포인트: 회원가입 / 로그인 / 토큰 리프레시 / 로그아웃 / 현재 사용자.

HttpOnly cookie 로 토큰 주입 (XSS 방어, Eng Review 설계).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.security import (
    SecurityError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from api.db.models import User
from api.db.session import get_db
from api.deps import ACCESS_COOKIE, REFRESH_COOKIE, CurrentUser
from api.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    """HttpOnly cookie 세팅. production 에서 secure=True 권장."""
    is_prod = settings.env == "production"
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access,
        max_age=settings.jwt_access_ttl_seconds,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh,
        max_age=settings.jwt_refresh_ttl_seconds,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/auth/refresh",  # refresh 엔드포인트에만 전송
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/auth/refresh")


async def _issue_tokens_for(user: User, response: Response) -> TokenResponse:
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    _set_auth_cookies(response, access, refresh)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """관세사 회원가입. 이메일 중복 시 409."""
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 이메일입니다."
        )

    user = User(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role="broker",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return await _issue_tokens_for(user, response)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """이메일·비밀번호 로그인. 성공 시 HttpOnly cookie + body 로 토큰 반환."""
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        # 존재 유무 누출 방지 (타이밍 공격 대비)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="비활성 계정입니다."
        )
    return await _issue_tokens_for(user, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    """Refresh 토큰으로 access 토큰 재발급."""
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="리프레시 토큰 없음"
        )
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = uuid.UUID(payload["sub"])
    except (SecurityError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자 없음"
        )
    return await _issue_tokens_for(user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    _clear_auth_cookies(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    """현재 로그인한 관세사 정보."""
    return user
