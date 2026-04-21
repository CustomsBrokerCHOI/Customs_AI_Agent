"""분류 요청·조회 엔드포인트.

UX 흐름 (Design Review Pass 3):
  1. 관세사가 품명·설명 입력 → POST /classify → 202 + job_id
  2. 클라이언트가 GET /classify/{id} 를 2-3초마다 폴링
  3. 백그라운드에서 ClassifyEngine 이 5단계 실행 (현재 stub)
  4. status=complete 되면 Top-3 후보 + 근거 조항 반환
  5. 관세사가 확인/거절 → POST /classify/{id}/review

실엔진 (services/classify_engine.py::run) 은 Week 4-5 에 연결.
현재는 stub 이 mock 결과를 2초 후 저장하여 폴링 UX 만 검증.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import SessionLocal
from api.db.models import ClassifyJob
from api.db.session import get_db
from api.deps import CurrentUser
from api.schemas.classify import (
    ClassifyRequest,
    ClassifyResult,
    JobCreateResponse,
    JobStatusResponse,
    ReviewRequest,
)
from api.services.classify_engine import ClassifyInput, mock_result

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/classify", tags=["classify"])


async def _run_classify_job(job_id: uuid.UUID) -> None:
    """백그라운드 분류 실행. 자체 DB 세션 사용 (요청 컨텍스트 종료 후 실행)."""
    async with SessionLocal() as db:
        job = await db.get(ClassifyJob, job_id)
        if job is None:
            logger.warning("Job %s not found in background task", job_id)
            return

        job.status = "processing"
        await db.commit()

        try:
            # 실엔진 대기: Week 4-5에 교체
            # from api.services.classify_engine import run as engine_run
            # result = await engine_run(ClassifyInput(...), db, ...)
            await asyncio.sleep(2)  # mock LLM 지연
            _inp = ClassifyInput(
                product_name=job.product_name, description=job.description
            )
            result = mock_result(_inp.product_name)

            job.result = {
                "candidates": [c.__dict__ for c in result.candidates],
                "notice": result.notice,
                "meta": result.meta,
            }
            job.status = "complete"
            job.completed_at = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Classify job %s failed", job_id)
            job.status = "failed"
            job.error_message = str(exc)[:500]
            job.completed_at = datetime.now(timezone.utc)

        await db.commit()


@router.post(
    "",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_classify_job(
    payload: ClassifyRequest,
    background: BackgroundTasks,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobCreateResponse:
    """분류 요청 생성 (비동기). 즉시 job_id 반환 → 클라이언트는 GET /classify/{id} 폴링."""
    # TODO (Week 6): 일일 rate limit 체크 (RATE_LIMIT_PER_DAY)
    # TODO (Week 4-5): LLM 호출 한도·비용 모니터링
    job = ClassifyJob(
        user_id=user.id,
        product_name=payload.product_name,
        description=payload.description,
        image_url=payload.image_url,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background.add_task(_run_classify_job, job.id)
    return JobCreateResponse(
        job_id=job.id, status=job.status, created_at=job.created_at
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_classify_job(
    job_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassifyJob:
    """분류 상태·결과 조회. 소유자만 접근 가능."""
    job = await db.get(ClassifyJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="분류 작업 없음"
        )
    if job.user_id != user.id:
        # 타 사용자 존재 유무 누출 방지 → 404 로 통일
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="분류 작업 없음"
        )
    return job


@router.post("/{job_id}/review", response_model=JobStatusResponse)
async def review_classify_job(
    job_id: uuid.UUID,
    payload: ReviewRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassifyJob:
    """관세사 확인·채택. Design Review 결정: reviewed=True 영속 저장."""
    job = await db.get(ClassifyJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="분류 작업 없음"
        )
    if job.status != "complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"완료 상태만 review 가능 (현재: {job.status})",
        )

    job.reviewed = True
    job.reviewed_at = datetime.now(timezone.utc)
    if payload.rejected:
        job.accepted_hs_code = None
    elif payload.accepted_hs_code:
        job.accepted_hs_code = payload.accepted_hs_code
    await db.commit()
    await db.refresh(job)
    return job
