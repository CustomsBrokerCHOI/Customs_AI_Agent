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


PASSWORD_MIN_LEN = 10
PASSWORD_MAX_LEN = 128
PASSWORD_MIN_CHAR_CLASSES = 2  # 소문자/대문자/숫자/특수문자 중 2개 이상


def validate_password_strength(password: str) -> None:
    """비밀번호 정책 검증. 실패 시 ``ValueError``.

    정책:
    - 길이 ``PASSWORD_MIN_LEN`` ~ ``PASSWORD_MAX_LEN``
    - 소문자 / 대문자 / 숫자 / 특수문자 4개 클래스 중 **최소 2개** 포함
    - 공백 문자 전체만으로 구성되지 않음
    """
    if password is None:
        raise ValueError("비밀번호가 비어있습니다.")
    if len(password) < PASSWORD_MIN_LEN:
        raise ValueError(f"비밀번호는 {PASSWORD_MIN_LEN}자 이상이어야 합니다.")
    if len(password) > PASSWORD_MAX_LEN:
        raise ValueError(f"비밀번호는 {PASSWORD_MAX_LEN}자 이하여야 합니다.")
    if not password.strip():
        raise ValueError("공백만으로 된 비밀번호는 허용되지 않습니다.")

    classes = 0
    if any(c.islower() for c in password):
        classes += 1
    if any(c.isupper() for c in password):
        classes += 1
    if any(c.isdigit() for c in password):
        classes += 1
    # 특수문자: 알파벳·숫자·공백이 아닌 문자
    if any(not c.isalnum() and not c.isspace() for c in password):
        classes += 1

    if classes < PASSWORD_MIN_CHAR_CLASSES:
        raise ValueError(
            f"비밀번호는 소문자/대문자/숫자/특수문자 중 최소 "
            f"{PASSWORD_MIN_CHAR_CLASSES} 종류를 포함해야 합니다."
        )


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
