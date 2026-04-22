"""인증 엔드포인트: 회원가입 / 로그인 / 토큰 리프레시 / 로그아웃 / 현재 사용자.

HttpOnly cookie 로 토큰 주입 (XSS 방어, Eng Review 설계).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
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
from api.services.account_lock import (
    MAX_FAILED_LOGINS,
    is_locked,
    record_failure,
    record_success,
    seconds_until_unlock,
)
from api.services.audit import (
    ACTION_ACCOUNT_LOCKED,
    ACTION_LOGIN,
    ACTION_LOGIN_FAILED,
    ACTION_REGISTER,
    RESOURCE_USER,
    get_client_ip,
    record_audit,
)

logger = logging.getLogger(__name__)

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
    request: Request,
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
    await db.flush()  # user.id 확보

    try:
        await record_audit(
            db,
            action=ACTION_REGISTER,
            user_id=user.id,
            resource_type=RESOURCE_USER,
            resource_id=str(user.id),
            metadata={"email": payload.email},
            ip_address=get_client_ip(request),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit register 실패 user=%s", user.id)

    await db.commit()
    await db.refresh(user)
    return await _issue_tokens_for(user, response)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """이메일·비밀번호 로그인. 성공 시 HttpOnly cookie + body 로 토큰 반환.

    브루트포스 방어: ``MAX_FAILED_LOGINS`` 회 연속 실패 시 일시 잠금 (423).
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    # 1) 사용자 없음 — 타이밍/존재 누출 방지 (카운터도 없음, 그대로 401)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    # 2) 이미 잠금 상태인지 먼저 검사 (비밀번호가 맞아도 거부)
    if is_locked(user):
        retry = seconds_until_unlock(user)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"반복 실패로 계정이 일시 잠겼습니다. 약 {retry}초 후 다시 시도하세요.",
            headers={"Retry-After": str(retry)},
        )

    # 3) 비밀번호 검증
    if not verify_password(payload.password, user.password_hash):
        newly_locked = record_failure(user)
        action = ACTION_ACCOUNT_LOCKED if newly_locked else ACTION_LOGIN_FAILED
        try:
            await record_audit(
                db,
                action=action,
                user_id=user.id,
                resource_type=RESOURCE_USER,
                resource_id=str(user.id),
                metadata={"failed_count": user.failed_login_count},
                ip_address=get_client_ip(request),
            )
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("audit login_failed 실패 user=%s", user.id)
            await db.rollback()

        if newly_locked:
            retry = seconds_until_unlock(user)
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=(
                    f"연속 {MAX_FAILED_LOGINS}회 실패로 계정이 일시 잠겼습니다. "
                    f"약 {retry}초 후 다시 시도하세요."
                ),
                headers={"Retry-After": str(retry)},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    # 4) 비활성 계정
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성 계정입니다.")

    # 5) 성공 — 카운터 리셋 + 감사
    record_success(user)
    try:
        await record_audit(
            db,
            action=ACTION_LOGIN,
            user_id=user.id,
            resource_type=RESOURCE_USER,
            resource_id=str(user.id),
            ip_address=get_client_ip(request),
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("audit login 실패 user=%s", user.id)
        await db.rollback()

    return await _issue_tokens_for(user, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    """Refresh 토큰으로 access 토큰 재발급."""
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="리프레시 토큰 없음")
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = uuid.UUID(payload["sub"])
    except (SecurityError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자 없음")
    return await _issue_tokens_for(user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    _clear_auth_cookies(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    """현재 로그인한 관세사 정보."""
    return user
