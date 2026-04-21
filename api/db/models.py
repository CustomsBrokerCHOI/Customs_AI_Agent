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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="broker", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    jobs: Mapped[list[ClassifyJob]] = relationship(back_populates="user")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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
        UniqueConstraint(
            "hs_code", "fta_code", "apply_start", name="uq_tariff_hs_fta_start"
        ),
        Index("idx_tariff_lookup", "hs_code", "fta_code"),
    )


# ---------- 해설서 + 임베딩 ----------


class ExplanatoryNote(Base):
    """부·류·호 주 및 해설서 원문. CLIP fetch_explanatory_note 소스."""

    __tablename__ = "explanatory_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    heading: Mapped[str] = mapped_column(CHAR(4), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # general_rule/section_note/chapter_note/heading_note
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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

    __table_args__ = (
        UniqueConstraint("model_name", "model_version", name="uq_emb_model_version"),
    )
