"""분류 요청·조회 엔드포인트.

UX 흐름 (Design Review Pass 3):
  1. 관세사가 품명·설명 입력 → POST /classify → 202 + job_id
  2. 클라이언트가 GET /classify/{id} 를 2-3초마다 폴링
  3. 백그라운드에서 ClassifyEngine 이 5단계 실행 (실엔진 또는 stub)
  4. status=complete 되면 Top-N 후보 + 근거 조항 반환
  5. 관세사가 확인/거절 → POST /classify/{id}/review

실엔진 vs stub 선택: Anthropic + OpenAI 키 모두 존재하면 실엔진, 아니면 mock.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.db.base import SessionLocal
from api.db.models import ClassifyJob
from api.db.session import get_db
from api.deps import CurrentUser
from api.schemas.classify import (
    ClassifyRequest,
    JobCreateResponse,
    JobStatusResponse,
    JobSummary,
    ReviewRequest,
)
from api.services.audit import (
    ACTION_CLASSIFY_CREATE,
    ACTION_CLASSIFY_REPORT_PDF,
    ACTION_CLASSIFY_REVIEW,
    RESOURCE_CLASSIFY_JOB,
    get_client_ip,
    record_audit,
    truncate_meta_text,
)
from api.services.classify_engine import (
    ClassifyInput,
    mock_result,
    result_to_dict,
    run as engine_run,
)
from api.services.llm_client import UsageLogger
from api.services.rate_limit import check_classify_rate_limit
from api.services.report import (
    PDFUnavailable,
    render_html_report,
    render_pdf_report,
    report_filename,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/classify", tags=["classify"])


def _use_real_engine() -> bool:
    """Anthropic + OpenAI 키가 모두 세팅되어 있으면 실엔진 사용."""
    return bool(settings.anthropic_api_key and settings.openai_api_key)


async def _run_classify_job(job_id: uuid.UUID) -> None:
    """백그라운드 분류 실행. 자체 DB 세션 사용 (요청 컨텍스트 종료 후 실행).

    Anthropic + OpenAI 키 모두 있으면 실엔진(5단계 RAG), 아니면 mock.
    """
    async with SessionLocal() as db:
        job = await db.get(ClassifyJob, job_id)
        if job is None:
            logger.warning("Job %s not found in background task", job_id)
            return

        job.status = "processing"
        await db.commit()

        try:
            inp = ClassifyInput(
                product_name=job.product_name,
                description=job.description,
                image_url=job.image_url,
                hsk_year=job.hsk_year,
            )
            if _use_real_engine():
                logger.info("Job %s: using real engine", job_id)
                result = await engine_run(inp, db)
            else:
                logger.info("Job %s: API keys missing — falling back to mock", job_id)
                await asyncio.sleep(2)
                result = mock_result(inp.product_name)

            job.result = result_to_dict(result)
            job.status = "complete"
            job.completed_at = datetime.now(timezone.utc)

            # Usage 영속 로그 — 분류 실패 시에도 토큰은 이미 썼을 수 있으나
            # 현재는 성공 경로에서만 기록. 로거 자체 실패는 분류를 깨지 않음.
            try:
                UsageLogger().log_job(
                    job_id=str(job.id),
                    user_id=str(job.user_id),
                    engine_result_meta=result.meta,
                    notice=result.notice,
                )
            except Exception:  # noqa: BLE001
                logger.exception("UsageLogger 기록 실패 job=%s", job.id)
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
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobCreateResponse:
    """분류 요청 생성 (비동기). 즉시 job_id 반환 → 클라이언트는 GET /classify/{id} 폴링.

    일일 한도(``settings.rate_limit_per_day``) 초과 시 429 + ``Retry-After`` 헤더
    (UTC 자정까지 남은 초).
    """
    allowed, used_today, retry_after = await check_classify_rate_limit(
        db, user.id, settings.rate_limit_per_day
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"일일 분류 한도 초과 ({used_today}/{settings.rate_limit_per_day}). "
                f"UTC 자정 이후 다시 시도하세요."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    job = ClassifyJob(
        user_id=user.id,
        product_name=payload.product_name,
        description=payload.description,
        image_url=payload.image_url,
        status="pending",
    )
    db.add(job)
    await db.flush()  # job.id 확보

    try:
        await record_audit(
            db,
            action=ACTION_CLASSIFY_CREATE,
            user_id=user.id,
            resource_type=RESOURCE_CLASSIFY_JOB,
            resource_id=str(job.id),
            metadata={
                "product_name": truncate_meta_text(payload.product_name),
                "used_today": used_today + 1,
            },
            ip_address=get_client_ip(request),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit classify.create 실패 job=%s", job.id)

    await db.commit()
    await db.refresh(job)

    background.add_task(_run_classify_job, job.id)
    return JobCreateResponse(job_id=job.id, status=job.status, created_at=job.created_at)


@router.get("", response_model=list[JobSummary])
async def list_classify_jobs(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 20,
    offset: int = 0,
) -> list[ClassifyJob]:
    """내 분류 잡 목록 — 최신순. ``limit`` 은 최대 100 으로 클램프."""
    from sqlalchemy import select as _select

    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    stmt = (
        _select(ClassifyJob)
        .where(ClassifyJob.user_id == user.id)
        .order_by(ClassifyJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_classify_job(
    job_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassifyJob:
    """분류 상태·결과 조회. 소유자만 접근 가능."""
    job = await db.get(ClassifyJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="분류 작업 없음")
    if job.user_id != user.id:
        # 타 사용자 존재 유무 누출 방지 → 404 로 통일
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="분류 작업 없음")
    return job


@router.post("/{job_id}/review", response_model=JobStatusResponse)
async def review_classify_job(
    job_id: uuid.UUID,
    payload: ReviewRequest,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassifyJob:
    """관세사 확인·채택. Design Review 결정: reviewed=True 영속 저장."""
    job = await db.get(ClassifyJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="분류 작업 없음")
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

    try:
        await record_audit(
            db,
            action=ACTION_CLASSIFY_REVIEW,
            user_id=user.id,
            resource_type=RESOURCE_CLASSIFY_JOB,
            resource_id=str(job.id),
            metadata={
                "rejected": bool(payload.rejected),
                "accepted_hs_code": job.accepted_hs_code,
            },
            ip_address=get_client_ip(request),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit classify.review 실패 job=%s", job.id)

    await db.commit()
    await db.refresh(job)
    return job


async def _load_owned_job(job_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> ClassifyJob:
    """소유자 검증 + 완료 상태 확인. 둘 다 404/409 통일."""
    job = await db.get(ClassifyJob, job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="분류 작업 없음")
    if job.status != "complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"완료 상태만 보고서 생성 가능 (현재: {job.status})",
        )
    return job


@router.get("/{job_id}/report.html", response_class=HTMLResponse)
async def get_report_html(
    job_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HTMLResponse:
    """분류의견서 HTML 렌더. 브라우저에서 직접 보거나 Ctrl+P 로 PDF 저장."""
    job = await _load_owned_job(job_id, user.id, db)
    html_str = render_html_report(job)
    return HTMLResponse(content=html_str, status_code=200)


@router.get("/{job_id}/report.pdf")
async def get_report_pdf(
    job_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """분류의견서 PDF 다운로드. weasyprint 필요 (없으면 501)."""
    job = await _load_owned_job(job_id, user.id, db)
    html_str = render_html_report(job)
    try:
        pdf_bytes = render_pdf_report(html_str)
    except PDFUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    try:
        await record_audit(
            db,
            action=ACTION_CLASSIFY_REPORT_PDF,
            user_id=user.id,
            resource_type=RESOURCE_CLASSIFY_JOB,
            resource_id=str(job.id),
            metadata={"bytes": len(pdf_bytes)},
            ip_address=get_client_ip(request),
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("audit classify.report_pdf 실패 job=%s", job.id)
        await db.rollback()

    filename = report_filename(job, "pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
