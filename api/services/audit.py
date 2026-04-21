"""Phase 4-A: 감사 로그 헬퍼.

설계 결정
---------
- **공용 헬퍼 한 개**: ``record_audit(db, action, ...)``. 상위 트랜잭션에 묶이도록
  ``flush`` 만 수행. commit 은 호출자 책임.
- **액션 상수**: 문자열 오타 방지. 이름 규칙 ``<domain>.<verb>`` (e.g.,
  ``auth.login``, ``classify.create``).
- **클라이언트 IP**: ``X-Forwarded-For`` 있으면 첫 값, 없으면 ``request.client.host``.
- **metadata**: JSON 컬럼. 민감 데이터(비밀번호·결제 정보 등) 저장 금지. 품명
  같은 일반 텍스트는 상한 200자로 잘라서 보관.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AuditLog

logger = logging.getLogger(__name__)


# 표준 액션 상수 — 문자열 오타 방지
ACTION_REGISTER = "auth.register"
ACTION_LOGIN = "auth.login"
ACTION_LOGIN_FAILED = "auth.login_failed"
ACTION_ACCOUNT_LOCKED = "auth.account_locked"
ACTION_CLASSIFY_CREATE = "classify.create"
ACTION_CLASSIFY_REVIEW = "classify.review"
ACTION_CLASSIFY_REPORT_PDF = "classify.report_pdf"


# 리소스 타입
RESOURCE_USER = "user"
RESOURCE_CLASSIFY_JOB = "classify_job"


MAX_METADATA_TEXT = 200


def get_client_ip(request: Request) -> str | None:
    """``X-Forwarded-For`` 우선 (첫 값), 비어있거나 공백만이면 ``request.client.host`` fallback."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = request.client
    return client.host if client else None


def truncate_meta_text(text: str | None, limit: int = MAX_METADATA_TEXT) -> str | None:
    if text is None:
        return None
    s = str(text)
    return s if len(s) <= limit else s[:limit]


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """``audit_logs`` 한 행 기록. 커밋은 호출자가.

    실패해도 상위 트랜잭션을 깨지 않도록 호출자가 ``try/except`` 로 감쌀 것.
    """
    row = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_=metadata,
        ip_address=ip_address,
    )
    db.add(row)
    try:
        await db.flush()
    except Exception:  # noqa: BLE001
        logger.exception("audit flush 실패 action=%s", action)
        raise
    return row
