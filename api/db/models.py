"""ORM 모델 (Phase 3-B 검색, Phase 4-A API 근거 데이터).

설계 근거: `~/.gstack/projects/Customs_AI_Agent/jiyop-*-design-*.md` 의 Eng Review.

9 테이블:
- users, audit_logs            (인증·감사)
- hs_codes, tariff_rates       (HS 마스터 + FTA 세율)
- explanatory_notes,
  note_chunks                  (해설서 + 임베딩)
- classification_cases         (품목분류 사례 + 임베딩)
- classify_jobs                (분류 요청 상태·결과)
- embedding_versions           (Dual 모델 + 버전 mismatch 감지)

- ``note_chunks`` / ``classification_cases`` 에는 pgvector ``Vector(1536)`` 컬럼.
  실제 인덱스(HNSW, cosine_ops)는 Alembic 마이그레이션에서 CONCURRENTLY 로 생성.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base

# ---------- 인증 / 감사 ----------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="broker", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 브루트포스 방어 (Phase 5-B) — api/services/account_lock.py 가 관리
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    jobs: Mapped[list[ClassifyJob]] = relationship(back_populates="user")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index("idx_audit_user_time", AuditLog.user_id, AuditLog.created_at.desc())


# ---------- HS 마스터 ----------


class HSCode(Base):
    """HS 10자리 품목 마스터. UNIPASS search_hs_sgn 소스."""

    __tablename__ = "hs_codes"

    hs_code: Mapped[str] = mapped_column(CHAR(10), primary_key=True)
    heading: Mapped[str] = mapped_column(CHAR(4), nullable=False, index=True)
    sub_heading: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    tariff_line: Mapped[str] = mapped_column(CHAR(4), nullable=False)
    name_kr: Mapped[str | None] = mapped_column(Text)
    name_en: Mapped[str | None] = mapped_column(Text)
    qty_unit: Mapped[str | None] = mapped_column(String(10))
    weight_unit: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20), default="UNIPASS")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tariff_rates: Mapped[list[TariffRate]] = relationship(back_populates="hs")


class TariffRate(Base):
    """FTA·세율종류별 관세율. UNIPASS retrieve_trrt 소스 (주), CLIP fetch_tariff_schedule (보조)."""

    __tablename__ = "tariff_rates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hs_code: Mapped[str] = mapped_column(
        CHAR(10), ForeignKey("hs_codes.hs_code", ondelete="CASCADE"), nullable=False
    )
    fta_code: Mapped[str] = mapped_column(String(10), nullable=False)  # A, FEU1, FUS1 ...
    fta_name: Mapped[str | None] = mapped_column(String(200))
    tax_rate: Mapped[float | None] = mapped_column(Numeric(10, 4))
    per_unit_tax: Mapped[float | None] = mapped_column(Numeric(15, 4))
    base_price: Mapped[float | None] = mapped_column(Numeric(15, 4))
    apply_start: Mapped[datetime | None] = mapped_column(Date)
    apply_end: Mapped[datetime | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20), default="UNIPASS")

    hs: Mapped[HSCode] = relationship(back_populates="tariff_rates")

    __table_args__ = (
        UniqueConstraint("hs_code", "fta_code", "apply_start", name="uq_tariff_hs_fta_start"),
        Index("idx_tariff_lookup", "hs_code", "fta_code"),
    )


# ---------- 해설서 + 임베딩 ----------


class ExplanatoryNote(Base):
    """부·류·호 주 및 해설서 원문. CLIP fetch_explanatory_note 소스."""

    __tablename__ = "explanatory_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    heading: Mapped[str] = mapped_column(CHAR(4), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # general_rule/section_note/chapter_note/heading_note
    lang: Mapped[str] = mapped_column(String(2), nullable=False)  # ko / en
    hsk_year: Mapped[int] = mapped_column(Integer, nullable=False, default=2022)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="CLIP")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunks: Mapped[list[NoteChunk]] = relationship(
        back_populates="note", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("heading", "kind", "lang", "hsk_year", name="uq_note_scope"),
    )


class NoteChunk(Base):
    """해설서 tiktoken 청크 + 임베딩. HNSW 인덱스는 마이그레이션에서 생성."""

    __tablename__ = "note_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("explanatory_notes.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(50))  # text-3-large / bge-m3-ko
    embedding_version: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    note: Mapped[ExplanatoryNote] = relationship(back_populates="chunks")


# ---------- 품목분류 사례 ----------


class ClassificationCase(Base):
    """관세청 품목분류 사례 (Phase 1-B openULS0203042S.do 스크래퍼 소스)."""

    __tablename__ = "classification_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hs_code: Mapped[str | None] = mapped_column(
        CHAR(10), ForeignKey("hs_codes.hs_code", ondelete="SET NULL"), index=True
    )
    case_ref: Mapped[str | None] = mapped_column(String(50), unique=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    decision_date: Mapped[datetime | None] = mapped_column(Date)
    reasoning: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(50))
    embedding_version: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(500))


# ---------- 분류 작업 ----------


class ClassifyJob(Base):
    """관세사가 제출한 분류 요청 + 결과."""

    __tablename__ = "classify_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500))

    # Status machine: pending → processing → complete | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)  # Top-3 후보 + 근거
    error_message: Mapped[str | None] = mapped_column(Text)

    # 관세사 확인 상태 (Draft → Confirmed / Rejected)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_hs_code: Mapped[str | None] = mapped_column(CHAR(10))

    # 입력 시점의 HSK 버전 (HS 개정 5년 주기 대응)
    hsk_year: Mapped[int] = mapped_column(Integer, default=2022, nullable=False)

    # Input Gate follow-up 우회 플래그. True 면 엔진은 needs_more_info 여부와
    # 무관하게 원본 입력만으로 경합 후보를 확대(최대 5개) 반환한다.
    force_classify: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 관세사가 제시한 최소 신뢰도 컷오프 (정수 %, 허용 31~69). NULL 이면 엔진 기본값(30%).
    min_confidence_pct: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="jobs")


Index("idx_jobs_user_created", ClassifyJob.user_id, ClassifyJob.created_at.desc())


# ---------- 임베딩 버전 관리 ----------


class EmbeddingVersion(Base):
    """임베딩 모델 버전 관리 (mismatch 감지용, Eng Review failure gap 대응)."""

    __tablename__ = "embedding_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False)  # text-3-large, bge-m3-ko
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("model_name", "model_version", name="uq_emb_model_version"),)


# ---------- HS 계층 구조 노드 (Sprint A) ----------


class HSNode(Base):
    """부·류·호·소호·10자리 HS 계층 노드.

    5인 회의 합의 스키마. level 컬럼 하나로 모든 계층을 단일 테이블에 담는다 —
    5개 분리 테이블 JOIN 지옥 회피 + 조회 통일.

    설계 근거:
    - version 을 PK 일부로 → HS 개정(5년 주기) 마이그레이션 없이 데이터 교체로 흡수.
    - notes_excerpt 원문 가공 금지 (법무: 증거능력 · 관세사: 해설서 신뢰 · LLM: 프롬프트 주입).
    - exclusion_keywords 는 substring 전용 룩업 (결정적 배제 판정).
    - inclusion_keywords 는 1차 필터 후 embedding 으로 2차 랭킹.
    - essential_character: 재질 / 용도 / 기능 중 이 노드가 어떤 축으로 분기하는지 (통칙 3나 전제).
    - processing_stage: 원료 / 반제품 / 완제품 (통칙 2(a) 적용 판단).
    - source_reference: 주 조문 위치. 3년 뒤 쟁송 시 역추적 근거.
    """

    __tablename__ = "hs_nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(20), nullable=False)  # 'HSK-2022'
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 2|4|6|10
    code: Mapped[str] = mapped_column(String(10), nullable=False)  # '84', '8471', '847130', '8471300000'
    parent_code: Mapped[str | None] = mapped_column(String(10))

    title_ko: Mapped[str] = mapped_column(Text, nullable=False)
    title_en: Mapped[str | None] = mapped_column(Text)

    # 부주·류주 원문 — 법무 요구: 가공 금지. LLM 프롬프트 주입용.
    notes_excerpt: Mapped[str] = mapped_column(Text, nullable=False)

    # 자동 파싱 결과 (정규식 + LLM). 빈 배열 허용.
    inclusion_keywords: Mapped[list[str] | None] = mapped_column(JSON)
    exclusion_keywords: Mapped[list[str] | None] = mapped_column(JSON)

    # 관세사 의견: 본질적 특성 축 (재질 / 용도 / 기능) + 가공도 단계.
    essential_character: Mapped[str | None] = mapped_column(String(50))
    processing_stage: Mapped[str | None] = mapped_column(String(30))

    # 법무 의견: 주 조문 위치 (예: "제16부 주 1 (가)").
    source_reference: Mapped[str | None] = mapped_column(String(200))

    # 포함 기준 + 해설서 의미 유사도. 2차 랭킹용.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(50))
    embedding_version: Mapped[int | None] = mapped_column(Integer)

    valid_from: Mapped[datetime] = mapped_column(Date, nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    discriminators: Mapped[list[HSSiblingDiscriminator]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )
    common_errors: Mapped[list[HSCommonError]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("version", "code", name="uq_hs_node_version_code"),
        Index("idx_hs_node_version_level", "version", "level"),
        Index("idx_hs_node_parent", "parent_code"),
    )


class HSSiblingDiscriminator(Base):
    """형제 노드 간 구분 근거 (경합호 구분논리).

    관세사 의견: 호 수준의 오분류 80% 는 "비슷한 류 2개 중 선택" 에서 발생.
    이 테이블이 재무가 꼽은 Top 2 ROI 필드의 절반.
    """

    __tablename__ = "hs_sibling_discriminators"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hs_nodes.id", ondelete="CASCADE"), nullable=False
    )
    vs_code: Mapped[str] = mapped_column(String(10), nullable=False)  # '8517' 같은 경합 코드
    discriminator: Mapped[str] = mapped_column(Text, nullable=False)  # "통신기능 유무"
    source_ref: Mapped[str | None] = mapped_column(String(200))

    node: Mapped[HSNode] = relationship(back_populates="discriminators")

    __table_args__ = (Index("idx_sibling_disc_node", "node_id"),)


class HSCommonError(Base):
    """자주 틀리는 오분류 패턴.

    재무가 꼽은 Top 2 ROI 필드 — 경쟁사 복제 불가 moat.
    Food Solution 축적 경험을 데이터화.
    """

    __tablename__ = "hs_common_errors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hs_nodes.id", ondelete="CASCADE"), nullable=False
    )
    mistake_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    correct_path: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100))  # '실무경험'|'결정례 2024-123'

    node: Mapped[HSNode] = relationship(back_populates="common_errors")

    __table_args__ = (Index("idx_common_err_node", "node_id"),)


class HSPrecedent(Base):
    """품목분류 선례 (대법원 판결 · 조세심판원 결정 · 관세평가분류원 사전회시 · 유사사례).

    법무 의견:
    - 구속력 등급 분리 필수 (대법원 4 > 조심원 3 > 사전회시 2 > 유사사례 1).
    - status 는 '참고만' / '적용불가' 두 값만. 자동 적용 금지 DB 차원 강제.
    - 관세법 제86조: 사전회시 기속력은 신청인-세관장 간에만 → 제3자에 대한 자동 적용은 법리적으로 부적절.
    """

    __tablename__ = "hs_precedents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    precedent_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # 'supreme_court'|'tax_tribunal'|'advance_ruling'|'similar_case'
    binding_level: Mapped[int] = mapped_column(Integer, nullable=False)  # 4|3|2|1
    case_no: Mapped[str] = mapped_column(String(100), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(Date)
    hs_code: Mapped[str | None] = mapped_column(String(10), index=True)

    summary: Mapped[str | None] = mapped_column(Text)
    full_text_ref: Mapped[str | None] = mapped_column(String(500))

    # 법무 요구: DB 차원 자동 적용 금지 강제.
    status: Mapped[str] = mapped_column(String(20), default="참고만", nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("precedent_type", "case_no", name="uq_precedent_type_case"),
        Index("idx_precedent_hscode", "hs_code", "binding_level"),
    )
