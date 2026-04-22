"""비밀번호 강도 policy 단위 테스트."""

from __future__ import annotations

import pytest

from api.core.security import (
    PASSWORD_MAX_LEN,
    PASSWORD_MIN_CHAR_CLASSES,
    PASSWORD_MIN_LEN,
    validate_password_strength,
)

# ---- 합격 케이스 ----


@pytest.mark.parametrize(
    "pw",
    [
        "StrongPass1",  # 영문 대소 + 숫자
        "password12!",  # 소문자 + 숫자 + 특수
        "UPPER123!!",  # 대문자 + 숫자 + 특수
        "aA1bB2cC3d",  # 대/소/숫자
        "관세사비밀번호!!1",  # 한국어 + 특수 + 숫자 (2 class 이상)
    ],
)
def test_valid_passwords_pass(pw: str) -> None:
    validate_password_strength(pw)  # raises 안 하면 OK


# ---- 실패: 길이 ----


def test_rejects_too_short() -> None:
    with pytest.raises(ValueError, match=f"{PASSWORD_MIN_LEN}"):
        validate_password_strength("aB1!x")


def test_rejects_too_long() -> None:
    with pytest.raises(ValueError, match=f"{PASSWORD_MAX_LEN}"):
        validate_password_strength("aB1!" * 40)


def test_rejects_whitespace_only() -> None:
    with pytest.raises(ValueError, match="공백"):
        validate_password_strength(" " * 12)


def test_rejects_none() -> None:
    with pytest.raises(ValueError, match="비어있"):
        validate_password_strength(None)  # type: ignore[arg-type]


# ---- 실패: 문자 클래스 부족 ----


@pytest.mark.parametrize(
    "pw",
    [
        "abcdefghij",  # 소문자만 (10자)
        "ABCDEFGHIJ",  # 대문자만
        "0123456789",  # 숫자만
        "!@#$%^&*()",  # 특수문자만
    ],
)
def test_rejects_single_class(pw: str) -> None:
    with pytest.raises(ValueError, match=str(PASSWORD_MIN_CHAR_CLASSES)):
        validate_password_strength(pw)


def test_accepts_exactly_min_classes() -> None:
    # 정확히 2 class (소문자 + 숫자)
    validate_password_strength("abcdefghij12")


# ---- RegisterRequest 통합 ----


def test_register_request_rejects_weak_password() -> None:
    # 지연 import (email-validator 있을 때만)
    pytest.importorskip("email_validator")
    from pydantic import ValidationError

    from api.schemas.auth import RegisterRequest

    with pytest.raises(ValidationError):
        RegisterRequest(email="broker@example.com", password="alllowercase", name="x")


def test_register_request_accepts_strong_password() -> None:
    pytest.importorskip("email_validator")
    from api.schemas.auth import RegisterRequest

    req = RegisterRequest(email="broker@example.com", password="StrongPass1!", name="관세사")
    assert req.password == "StrongPass1!"
