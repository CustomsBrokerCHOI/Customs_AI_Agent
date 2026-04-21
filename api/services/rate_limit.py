"""Phase 4-A: 일일 분류 요청 Rate Limit.

설계 결정
---------
- **윈도우**: UTC 00:00 기준 당일 ``classify_jobs`` 생성 건수. 자정 reset.
- **MVP 수준**: ``COUNT(*)`` SQL 한 번. 상위 쿼리와 분리된 race condition 가능
  (두 요청이 동시에 통과) — 실무 운영 시 Redis INCR/TTL 또는 DB UNIQUE 제약
  으로 강화. MVP 는 DB 자원 소모를 억제하는 정도로 충분.
- **상한**: ``settings.rate_limit_per_day`` (기본 100).
- **Retry-After**: 초과 시 UTC 자정까지 남은 초. 실제 응답 헤더로 노출.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ClassifyJob


def _today_start_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def seconds_until_utc_midnight(now: datetime | None = None) -> int:
    """UTC 다음 자정 (내일 00:00) 까지 남은 초. 429 ``Retry-After`` 헤더용."""
    now = now or datetime.now(timezone.utc)
    next_midnight = _today_start_utc(now) + timedelta(days=1)
    return max(0, int((next_midnight - now).total_seconds()))


async def count_today_classify_jobs(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    """``user_id`` 가 오늘 (UTC) 생성한 ``classify_jobs`` 건수."""
    start = _today_start_utc(now)
    stmt = select(func.count(ClassifyJob.id)).where(
        ClassifyJob.user_id == user_id,
        ClassifyJob.created_at >= start,
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def check_classify_rate_limit(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int,
    *,
    now: datetime | None = None,
) -> tuple[bool, int, int]:
    """한도 초과 여부 + 현재 사용량 + Retry-After 초.

    :returns: ``(allowed, used_today, retry_after_sec)``.
    """
    used = await count_today_classify_jobs(db, user_id, now=now)
    allowed = used < limit
    retry = seconds_until_utc_midnight(now) if not allowed else 0
    return allowed, used, retry
