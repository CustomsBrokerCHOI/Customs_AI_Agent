"""initial schema — 9 tables + pgvector extension + HNSW indexes

Revision ID: 0001
Revises:
Create Date: 2026-04-21

9 테이블:
- users, audit_logs
- hs_codes, tariff_rates
- explanatory_notes, note_chunks (+ HNSW index on embedding)
- classification_cases (+ HNSW index on embedding)
- classify_jobs
- embedding_versions

pgvector 확장 먼저 활성화, 그 다음 Vector(1536) 컬럼 + HNSW 인덱스 생성.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- pgvector 확장 ---
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(100)),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="broker"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(50)),
        sa.Column("resource_id", sa.String(100)),
        sa.Column("metadata", sa.JSON),
        sa.Column("ip_address", sa.String(45)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_audit_user_time",
        "audit_logs",
        ["user_id", sa.text("created_at DESC")],
    )

    # --- hs_codes ---
    op.create_table(
        "hs_codes",
        sa.Column("hs_code", sa.CHAR(10), primary_key=True),
        sa.Column("heading", sa.CHAR(4), nullable=False),
        sa.Column("sub_heading", sa.CHAR(2), nullable=False),
        sa.Column("tariff_line", sa.CHAR(4), nullable=False),
        sa.Column("name_kr", sa.Text),
        sa.Column("name_en", sa.Text),
        sa.Column("qty_unit", sa.String(10)),
        sa.Column("weight_unit", sa.String(10)),
        sa.Column("source", sa.String(20), server_default="UNIPASS"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_hs_codes_heading", "hs_codes", ["heading"])

    # --- tariff_rates ---
    op.create_table(
        "tariff_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hs_code",
            sa.CHAR(10),
            sa.ForeignKey("hs_codes.hs_code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fta_code", sa.String(10), nullable=False),
        sa.Column("fta_name", sa.String(200)),
        sa.Column("tax_rate", sa.Numeric(10, 4)),
        sa.Column("per_unit_tax", sa.Numeric(15, 4)),
        sa.Column("base_price", sa.Numeric(15, 4)),
        sa.Column("apply_start", sa.Date),
        sa.Column("apply_end", sa.Date),
        sa.Column("source", sa.String(20), server_default="UNIPASS"),
        sa.UniqueConstraint(
            "hs_code", "fta_code", "apply_start", name="uq_tariff_hs_fta_start"
        ),
    )
    op.create_index("idx_tariff_lookup", "tariff_rates", ["hs_code", "fta_code"])

    # --- explanatory_notes ---
    op.create_table(
        "explanatory_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("heading", sa.CHAR(4), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("lang", sa.String(2), nullable=False),
        sa.Column("hsk_year", sa.Integer, nullable=False, server_default="2022"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source", sa.String(20), server_default="CLIP"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("heading", "kind", "lang", "hsk_year", name="uq_note_scope"),
    )
    op.create_index(
        "ix_explanatory_notes_heading", "explanatory_notes", ["heading"]
    )

    # --- note_chunks ---
    op.create_table(
        "note_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "note_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("explanatory_notes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1536)),
        sa.Column("embedding_model", sa.String(50)),
        sa.Column("embedding_version", sa.Integer),
        sa.Column("metadata", sa.JSON),
    )
    # HNSW index for cosine similarity search
    op.execute(
        "CREATE INDEX idx_note_chunks_embedding_hnsw "
        "ON note_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # --- classification_cases ---
    op.create_table(
        "classification_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hs_code",
            sa.CHAR(10),
            sa.ForeignKey("hs_codes.hs_code", ondelete="SET NULL"),
        ),
        sa.Column("case_ref", sa.String(50), unique=True),
        sa.Column("product_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("decision_date", sa.Date),
        sa.Column("reasoning", sa.Text),
        sa.Column("embedding", Vector(1536)),
        sa.Column("embedding_model", sa.String(50)),
        sa.Column("embedding_version", sa.Integer),
        sa.Column("source_url", sa.String(500)),
    )
    op.create_index(
        "ix_classification_cases_hs_code", "classification_cases", ["hs_code"]
    )
    op.execute(
        "CREATE INDEX idx_classification_cases_embedding_hnsw "
        "ON classification_cases USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # --- classify_jobs ---
    op.create_table(
        "classify_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("image_url", sa.String(500)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON),
        sa.Column("error_message", sa.Text),
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_hs_code", sa.CHAR(10)),
        sa.Column("hsk_year", sa.Integer, nullable=False, server_default="2022"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "idx_jobs_user_created",
        "classify_jobs",
        ["user_id", sa.text("created_at DESC")],
    )

    # --- embedding_versions ---
    op.create_table(
        "embedding_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("model_name", sa.String(50), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "model_name", "model_version", name="uq_emb_model_version"
        ),
    )


def downgrade() -> None:
    # HNSW 인덱스는 테이블 drop 시 자동 제거
    op.drop_table("embedding_versions")
    op.drop_index("idx_jobs_user_created", table_name="classify_jobs")
    op.drop_table("classify_jobs")
    op.execute("DROP INDEX IF EXISTS idx_classification_cases_embedding_hnsw")
    op.drop_index("ix_classification_cases_hs_code", table_name="classification_cases")
    op.drop_table("classification_cases")
    op.execute("DROP INDEX IF EXISTS idx_note_chunks_embedding_hnsw")
    op.drop_table("note_chunks")
    op.drop_index("ix_explanatory_notes_heading", table_name="explanatory_notes")
    op.drop_table("explanatory_notes")
    op.drop_index("idx_tariff_lookup", table_name="tariff_rates")
    op.drop_table("tariff_rates")
    op.drop_index("ix_hs_codes_heading", table_name="hs_codes")
    op.drop_table("hs_codes")
    op.drop_index("idx_audit_user_time", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("users")
    # pgvector 확장은 유지 (다른 용도로 쓰일 수 있음)
