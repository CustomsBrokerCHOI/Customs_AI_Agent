"""Phase 5-B: 로그인 실패 카운터 + 계정 일시 잠금.

정책
----
- ``MAX_FAILED_LOGINS`` 회 (기본 5) 연속 실패 → ``LOCKOUT_DURATION_SEC``
  (기본 15분) 동안 잠금.
- 성공하면 카운터 0 + ``locked_until=None``.
- 잠금 중에는 비밀번호가 맞더라도 거부 (잠금 해제 대기).
- 잠금 해제 시각(UTC)이 지나면 자동으로 재시도 허용 (별도 unlock 액션 불필요).

MVP 수준 race condition 은 허용 (분산 락·Redis atomic 은 후속).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from api.db.models import User

logger = logging.getLogger(__name__)

# 정책 상수
MAX_FAILED_LOGINS = 5
LOCKOUT_DURATION_SEC = 15 * 60  # 15분


def is_locked(user: User, *, now: datetime | None = None) -> bool:
    """현재 ``user`` 가 잠금 상태인지."""
    if user.locked_until is None:
        return False
    now = now or datetime.now(timezone.utc)
    return user.locked_until > now


def seconds_until_unlock(user: User, *, now: datetime | None = None) -> int:
    """잠금 해제까지 남은 초. 잠금이 아니면 0."""
    if user.locked_until is None:
        return 0
    now = now or datetime.now(timezone.utc)
    delta = (user.locked_until - now).total_seconds()
    return max(0, int(delta))


def record_failure(
    user: User,
    *,
    max_failures: int = MAX_FAILED_LOGINS,
    lockout_sec: int = LOCKOUT_DURATION_SEC,
    now: datetime | None = None,
) -> bool:
    """로그인 실패 카운터 +1. 임계 도달 시 ``locked_until`` 세팅.

    :returns: 이번 실패로 "잠금이 새로 적용되었는가" 를 boolean 으로.
              (orchestrator 가 audit/응답 분기 판단에 사용).
    """
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= max_failures:
        now = now or datetime.now(timezone.utc)
        user.locked_until = now + timedelta(seconds=lockout_sec)
        logger.warning(
            "account locked user=%s count=%s until=%s",
            user.id,
            user.failed_login_count,
            user.locked_until,
        )
        return True
    return False


def record_success(user: User) -> None:
    """로그인 성공 — 카운터 0 + 잠금 해제."""
    user.failed_login_count = 0
    user.locked_until = None
