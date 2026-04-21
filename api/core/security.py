"""비밀번호 해시 + JWT 토큰 발급·검증.

JWT payload:
- sub: user.id (UUID str)
- type: "access" | "refresh"
- exp: 만료 timestamp
- iat: 발급 timestamp

HttpOnly cookie 로 주입 (XSS 방어). 토큰 자체는 서명만 검증, 권한은 DB lookup.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from api.core.config import settings


class SecurityError(Exception):
    """토큰 검증 실패, 해시 불일치 등."""


_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------- 비밀번호 ----------


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        return False


# ---------- JWT ----------


def _encode(payload: dict[str, Any], ttl_seconds: int) -> str:
    now = datetime.now(timezone.utc)
    body = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(body, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: uuid.UUID) -> str:
    return _encode(
        {"sub": str(user_id), "type": "access"},
        settings.jwt_access_ttl_seconds,
    )


def create_refresh_token(user_id: uuid.UUID) -> str:
    return _encode(
        {"sub": str(user_id), "type": "refresh"},
        settings.jwt_refresh_ttl_seconds,
    )


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """JWT 서명·만료·타입 검증. 실패 시 ``SecurityError``.

    :returns: decoded payload dict (``sub``, ``type``, ``exp``, ``iat``).
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise SecurityError(f"토큰 검증 실패: {exc}") from exc

    if payload.get("type") != expected_type:
        raise SecurityError(
            f"토큰 타입 불일치: expected={expected_type} got={payload.get('type')}"
        )
    if "sub" not in payload:
        raise SecurityError("토큰에 sub 클레임 없음")
    return payload
