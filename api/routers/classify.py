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
    EnrichCitationOut,
    EnrichRequest,
    EnrichResponse,
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


def _classify_single_error(raw: str) -> str | None:
    """단일 예외 문자열 → 친화 라벨. 매치 없으면 None."""
    low = raw.lower()
    if "credit balance" in low or "credit_balance" in low:
        return "Anthropic 크레딧 부족"
    # DeepSeek 잔액 부족: 402 + "Insufficient Balance"
    if "insufficient balance" in low or ("status_code: 402" in low and "deepseek" in low):
        return "DeepSeek 잔액 부족"
    if "status_code: 402" in low:
        return "LLM 잔액 부족(402)"
    if "insufficient_quota" in low or ("billing" in low and "not_found" in low):
        return "LLM 결제 설정 오류"
    if "invalid_api_key" in low or "authentication" in low and "fail" in low:
        return "LLM API 키 오류"
    if "status_code: 503" in low or ("unavailable" in low and "currently" in low):
        return "Gemini 일시 과부하(503)"
    if "status_code: 429" in low or "rate_limit_error" in low or "error code: 429" in raw:
        return "LLM rate limit"
    if "error code: 529" in raw or "overloaded_error" in low:
        return "Claude 혼잡(529)"
    return None


def _friendly_error_message(exc: Exception) -> str:
    """원시 예외 메시지를 관세사가 이해할 수 있는 형태로 변환.

    ``FallbackExceptionGroup`` 등 ExceptionGroup 은 각 프로바이더 실패 사유를 나열.
    전부 분류 실패하면 원문을 500자로 잘라 마지막 수단으로 노출.
    """
    # ExceptionGroup 언랩 (FallbackExceptionGroup 포함)
    subs = getattr(exc, "exceptions", None)
    if subs:
        labels: list[str] = []
        raws: list[str] = []
        for i, sub in enumerate(subs, 1):
            s = str(sub)
            raws.append(s)
            label = _classify_single_error(s)
            labels.append(label or f"오류 {i}: {s[:120]}")
        joined = " / ".join(labels)
        # 모든 폴백 실패 → 권장 조치도 함께.
        advice = ""
        if any("Anthropic 크레딧" in l for l in labels):
            advice += " · Anthropic Plans & Billing 에서 크레딧 충전."
        if any("DeepSeek 잔액" in l for l in labels):
            advice += " · DeepSeek platform.deepseek.com 에서 최소 $2 선충전."
        if any("rate limit" in l.lower() for l in labels):
            advice += " · Gemini Free-tier RPM 한도 초과 — 수 분 대기 후 재시도."
        if any("503" in l for l in labels):
            advice += " · Gemini 과부하는 수 초~수 분 후 자동 해소, 다시 시도."
        return f"분류 LLM 모두 실패: {joined}.{advice}".rstrip()

    # 단일 예외
    label = _classify_single_error(str(exc))
    if label == "Anthropic 크레딧 부족":
        return (
            "LLM API 크레딧 잔액이 부족해 분류를 완료할 수 없습니다. "
            "관리자에게 Anthropic Plans & Billing 에서 크레딧 충전 요청하세요."
        )
    if label == "LLM 결제 설정 오류":
        return "LLM API 결제 설정이 올바르지 않습니다. 관리자에게 API 키 · 결제 한도 확인 요청하세요."
    if label == "Gemini 일시 과부하(503)":
        return "Gemini API 가 일시 과부하 상태입니다(503). 잠시 뒤 재시도해 주세요."
    if label == "LLM rate limit":
        return "LLM 호출량이 분당 한도를 초과했습니다. 1~2분 뒤 다시 시도해 주세요."
    if label == "Claude 혼잡(529)":
        return "Claude API 가 일시 혼잡(529)입니다. 잠시 뒤 재시도해 주세요."
    return str(exc)[:500]


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
                force_classify=job.force_classify,
                min_confidence_pct=job.min_confidence_pct,
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
            job.error_message = _friendly_error_message(exc)
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
        force_classify=payload.force_classify,
        min_confidence_pct=payload.min_confidence_pct,
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
                "force_classify": bool(payload.force_classify),
                "min_confidence_pct": payload.min_confidence_pct,
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


@router.post("/enrich", response_model=EnrichResponse)
async def enrich_product(
    payload: EnrichRequest,
    user: CurrentUser,
) -> EnrichResponse:
    """Gemini Grounded Search 로 물품 정보 보강. 분류 엔진과 분리되어 있어 DB 를
    건드리지 않고 결과만 반환한다. 관세사가 description 으로 채택하면 별도
    POST /classify 로 분류 요청을 보낸다.

    GEMINI_API_KEY 미설정 시 503. 인증된 사용자만 호출 가능.
    """
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini 보강 기능이 활성화되지 않았습니다 (GEMINI_API_KEY 미설정).",
        )

    from api.services.gemini_enrich import enrich_product_info

    try:
        result = await enrich_product_info(
            product_name=payload.product_name,
            image_url=payload.image_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("gemini enrich 실패 user=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini 호출 실패: {str(exc)[:200]}",
        ) from exc

    return EnrichResponse(
        description=result.description,
        citations=[EnrichCitationOut(url=c.url, title=c.title) for c in result.citations],
        queries=result.queries,
        model=result.model,
    )


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
    """분류의견서 HTML 렌더. 유사 분류 사례 섹션을 위해 sync DB session 을 주입."""
    job = await _load_owned_job(job_id, user.id, db)
    html_str = await db.run_sync(lambda s: render_html_report(job, session=s))
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
    html_str = await db.run_sync(lambda s: render_html_report(job, session=s))
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
