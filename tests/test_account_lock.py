"""account_lock 서비스 단위 테스트."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from api.db.models import User
from api.services.account_lock import (
    LOCKOUT_DURATION_SEC,
    MAX_FAILED_LOGINS,
    is_locked,
    record_failure,
    record_success,
    seconds_until_unlock,
)


def _fresh_user() -> User:
    u = User(
        email="broker@example.com",
        name="tester",
        password_hash="hash",
        role="broker",
        is_active=True,
        failed_login_count=0,
    )
    u.id = uuid.uuid4()
    return u


# ---- is_locked ----


def test_is_locked_false_when_no_locked_until() -> None:
    u = _fresh_user()
    assert is_locked(u) is False
    assert seconds_until_unlock(u) == 0


def test_is_locked_true_when_future() -> None:
    u = _fresh_user()
    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    u.locked_until = now + timedelta(seconds=300)
    assert is_locked(u, now=now) is True
    assert seconds_until_unlock(u, now=now) == 300


def test_is_locked_false_when_past() -> None:
    u = _fresh_user()
    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    u.locked_until = now - timedelta(seconds=10)
    assert is_locked(u, now=now) is False
    assert seconds_until_unlock(u, now=now) == 0


def test_is_locked_false_at_exact_boundary() -> None:
    u = _fresh_user()
    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    u.locked_until = now  # 정확히 지금
    # > 연산이므로 False
    assert is_locked(u, now=now) is False


# ---- record_failure ----


def test_record_failure_increments_counter() -> None:
    u = _fresh_user()
    newly_locked = record_failure(u, max_failures=5)
    assert u.failed_login_count == 1
    assert newly_locked is False
    assert u.locked_until is None


def test_record_failure_accumulates() -> None:
    u = _fresh_user()
    for i in range(1, 5):
        newly = record_failure(u, max_failures=5)
        assert newly is False
        assert u.failed_login_count == i
        assert u.locked_until is None


def test_record_failure_triggers_lock_at_threshold() -> None:
    u = _fresh_user()
    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    for _ in range(4):
        record_failure(u, max_failures=5, now=now)

    # 5번째 실패 → 잠금
    newly = record_failure(u, max_failures=5, lockout_sec=900, now=now)
    assert newly is True
    assert u.failed_login_count == 5
    assert u.locked_until == now + timedelta(seconds=900)


def test_record_failure_beyond_threshold_keeps_lock() -> None:
    u = _fresh_user()
    u.failed_login_count = 5
    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    u.locked_until = now + timedelta(seconds=900)

    # 6번째 실패 (이미 잠긴 상태) — is_locked check 는 라우터 책임, 서비스는 누적만
    newly = record_failure(u, max_failures=5, lockout_sec=900, now=now)
    assert newly is True  # 임계 이상은 계속 True (재연장은 라우터 정책)
    assert u.failed_login_count == 6


def test_record_failure_handles_none_count() -> None:
    """DB 에서 NULL 로 읽혔다가 올라온 레거시 레코드 안전."""
    u = _fresh_user()
    u.failed_login_count = None  # type: ignore[assignment]
    record_failure(u)
    assert u.failed_login_count == 1


# ---- record_success ----


def test_record_success_resets_counter_and_lock() -> None:
    u = _fresh_user()
    u.failed_login_count = 3
    u.locked_until = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    record_success(u)
    assert u.failed_login_count == 0
    assert u.locked_until is None


def test_record_success_noop_on_fresh_user() -> None:
    u = _fresh_user()
    record_success(u)
    assert u.failed_login_count == 0
    assert u.locked_until is None


# ---- 정책 상수 ----


def test_default_max_failures_reasonable() -> None:
    assert 3 <= MAX_FAILED_LOGINS <= 10


def test_default_lockout_duration_reasonable() -> None:
    # 최소 1분 ~ 최대 2시간
    assert 60 <= LOCKOUT_DURATION_SEC <= 2 * 3600


# ---- 통합: 실패 후 시간 경과 시뮬레이션 ----


def test_lock_expires_after_duration() -> None:
    u = _fresh_user()
    t0 = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    for _ in range(5):
        record_failure(u, max_failures=5, lockout_sec=60, now=t0)
    assert is_locked(u, now=t0) is True

    # 60초 이상 경과 → 잠금 자동 해제 (is_locked=False)
    later = t0 + timedelta(seconds=61)
    assert is_locked(u, now=later) is False
    assert seconds_until_unlock(u, now=later) == 0


def test_successful_login_during_lock_window_clears_state() -> None:
    u = _fresh_user()
    t0 = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    for _ in range(5):
        record_failure(u, max_failures=5, now=t0)
    assert is_locked(u, now=t0) is True

    # 라우터가 is_locked 를 먼저 체크해 거부하는 것이 정상이지만, 서비스만 쓸 경우
    # record_success 호출하면 무조건 리셋.
    record_success(u)
    assert is_locked(u) is False
    assert u.failed_login_count == 0
