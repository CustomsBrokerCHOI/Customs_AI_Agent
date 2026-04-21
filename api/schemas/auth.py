"""인증 요청·응답 스키마."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str | None = Field(None, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str | None
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """로그인 응답. 토큰은 HttpOnly cookie 로 설정되지만 클라이언트가 만료·타입 참고용으로 사용."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    user: UserOut
