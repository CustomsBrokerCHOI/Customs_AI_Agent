"""Phase 4-A rate_limit + audit 단위 테스트 (네트워크/실DB 없음)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.audit import (
    ACTION_CLASSIFY_CREATE,
    MAX_METADATA_TEXT,
    RESOURCE_CLASSIFY_JOB,
    get_client_ip,
    record_audit,
    truncate_meta_text,
)
from api.services.rate_limit import (
    check_classify_rate_limit,
    count_today_classify_jobs,
    seconds_until_utc_midnight,
)

# ---- seconds_until_utc_midnight ----


def test_seconds_until_midnight_at_noon() -> None:
    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_utc_midnight(now) == 12 * 3600


def test_seconds_until_midnight_one_second_before() -> None:
    now = datetime(2026, 4, 22, 23, 59, 59, tzinfo=timezone.utc)
    assert seconds_until_utc_midnight(now) == 1


def test_seconds_until_midnight_at_midnight() -> None:
    # 정확히 자정이면 24*3600 (다음 자정까지)
    now = datetime(2026, 4, 22, 0, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_utc_midnight(now) == 24 * 3600


def test_seconds_until_midnight_non_utc_still_ok() -> None:
    # UTC 가 아닌 tz 가 들어와도 UTC 로 변환되어 계산되어야 안전
    from datetime import timezone as tz

    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=tz(timedelta(hours=9)))
    s = seconds_until_utc_midnight(now)
    # UTC 03:00 → 21 시간 남음
    assert s == 21 * 3600


# ---- count_today_classify_jobs ----


@pytest.mark.asyncio
async def test_count_today_returns_int() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one.return_value = 7
    db.execute = AsyncMock(return_value=result)

    n = await count_today_classify_jobs(db, uuid.uuid4())
    assert n == 7
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_count_today_returns_zero_when_none() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one.return_value = None
    db.execute = AsyncMock(return_value=result)

    n = await count_today_classify_jobs(db, uuid.uuid4())
    assert n == 0


# ---- check_classify_rate_limit ----


@pytest.mark.asyncio
async def test_check_rate_limit_allowed_when_under_quota() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one.return_value = 3
    db.execute = AsyncMock(return_value=result)

    now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    allowed, used, retry = await check_classify_rate_limit(db, uuid.uuid4(), limit=100, now=now)
    assert allowed is True
    assert used == 3
    assert retry == 0


@pytest.mark.asyncio
async def test_check_rate_limit_blocked_at_quota() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one.return_value = 100
    db.execute = AsyncMock(return_value=result)

    now = datetime(2026, 4, 22, 18, 0, 0, tzinfo=timezone.utc)
    allowed, used, retry = await check_classify_rate_limit(db, uuid.uuid4(), limit=100, now=now)
    assert allowed is False
    assert used == 100
    assert retry == 6 * 3600  # UTC 자정까지 6시간


# ---- get_client_ip ----


def _req(headers: dict | None = None, client_host: str | None = "203.0.113.1"):
    # 간단한 Request 대체 — get_client_ip 가 쓰는 부분만 stub
    class _FakeReq:
        def __init__(self):
            self.headers = _FakeHeaders(headers or {})
            self.client = SimpleNamespace(host=client_host) if client_host else None

    class _FakeHeaders:
        def __init__(self, d):
            self._d = {k.lower(): v for k, v in d.items()}

        def get(self, key, default=None):
            return self._d.get(key.lower(), default)

    return _FakeReq()


def test_get_client_ip_uses_xff_first_value() -> None:
    req = _req(headers={"X-Forwarded-For": "198.51.100.10, 10.0.0.1"})
    assert get_client_ip(req) == "198.51.100.10"


def test_get_client_ip_strips_xff_whitespace() -> None:
    req = _req(headers={"x-forwarded-for": "   198.51.100.10   "})
    assert get_client_ip(req) == "198.51.100.10"


def test_get_client_ip_falls_back_to_client_host() -> None:
    req = _req(headers={})
    assert get_client_ip(req) == "203.0.113.1"


def test_get_client_ip_none_when_no_source() -> None:
    req = _req(headers={}, client_host=None)
    assert get_client_ip(req) is None


def test_get_client_ip_empty_xff_falls_back() -> None:
    # 빈 XFF 는 무시하고 client.host
    req = _req(headers={"X-Forwarded-For": "   "})
    assert get_client_ip(req) == "203.0.113.1"


# ---- truncate_meta_text ----


def test_truncate_meta_text_caps_at_limit() -> None:
    s = "가" * 500
    out = truncate_meta_text(s)
    assert out is not None
    assert len(out) == MAX_METADATA_TEXT


def test_truncate_meta_text_none_passthrough() -> None:
    assert truncate_meta_text(None) is None


def test_truncate_meta_text_short_passthrough() -> None:
    assert truncate_meta_text("짧은 텍스트") == "짧은 텍스트"


# ---- record_audit ----


@pytest.mark.asyncio
async def test_record_audit_adds_row_with_fields() -> None:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    uid = uuid.uuid4()
    row = await record_audit(
        db,
        action=ACTION_CLASSIFY_CREATE,
        user_id=uid,
        resource_type=RESOURCE_CLASSIFY_JOB,
        resource_id="abc-123",
        metadata={"product_name": "노트북"},
        ip_address="203.0.113.1",
    )

    # 행 생성 + add/flush 호출
    db.add.assert_called_once()
    db.flush.assert_awaited_once()

    assert row.action == ACTION_CLASSIFY_CREATE
    assert row.user_id == uid
    assert row.resource_type == RESOURCE_CLASSIFY_JOB
    assert row.resource_id == "abc-123"
    assert row.metadata_ == {"product_name": "노트북"}
    assert row.ip_address == "203.0.113.1"


@pytest.mark.asyncio
async def test_record_audit_propagates_flush_failure() -> None:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await record_audit(db, action="x")
