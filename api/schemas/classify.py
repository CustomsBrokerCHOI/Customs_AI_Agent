"""분류 요청·결과 스키마."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ClassifyRequest(BaseModel):
    """관세사 분류 요청."""

    product_name: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=5000)
    image_url: str | None = Field(None, max_length=500)


class Citation(BaseModel):
    """근거 조항 인용 (해설서/주/통칙 원문)."""

    source_kind: str  # heading_note / section_note / chapter_note / general_rule / case
    heading: str | None = None  # 4자리 호
    excerpt: str  # 발췌 (200자)
    full_text_url: str | None = None  # 원문 보기


class Candidate(BaseModel):
    """Top-3 중 하나."""

    rank: int
    hs_code: str
    name_kr: str | None
    name_en: str | None
    heading: str  # 4자리
    sub_heading: str  # 2자리
    breadcrumb: list[str]  # 부→류→호→세번 각 단계 라벨
    confidence: float = Field(ge=0.0, le=1.0)
    base_tariff_rate: str | None = None  # FTA 'A' 세율
    verified: bool  # RAG 검증 성공 여부 (partial result 대응)
    citations: list[Citation]


class ClassifyResult(BaseModel):
    candidates: list[Candidate]
    notice: str | None = None  # 예: "분류 불확실 — 관세사 검토 요청"


class JobCreateResponse(BaseModel):
    """POST /classify 응답 (202)."""

    job_id: uuid.UUID
    status: str  # pending
    created_at: datetime


class JobStatusResponse(BaseModel):
    """GET /classify/{id} 응답."""

    id: uuid.UUID
    status: str  # pending / processing / complete / failed / cancelled
    product_name: str
    description: str
    result: ClassifyResult | None = None
    error_message: str | None = None
    reviewed: bool
    accepted_hs_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReviewRequest(BaseModel):
    """관세사 '확인함/채택' 표시."""

    accepted_hs_code: str | None = Field(None, min_length=10, max_length=10)
    rejected: bool = False
