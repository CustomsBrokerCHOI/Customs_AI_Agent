"""hs_nodes + sibling/common_error/precedent 테이블 (5인 회의 Sprint A).

계층적 HS 분류 구조화를 위한 4개 테이블 신설:
- hs_nodes: 부/류/호/소호/10자리 통합. level 컬럼 + version PK 일부.
- hs_sibling_discriminators: 경합호 구분논리 (Sprint B 충전).
- hs_common_errors: 자주 틀리는 오분류 패턴 (Sprint B 충전, moat 필드).
- hs_precedents: 선례 (구속력 등급 분리, 자동 적용 금지 DB 강제).

인덱스:
- hs_nodes: GIN on inclusion/exclusion_keywords (substring 룩업).
- hs_nodes: ivfflat on embedding (2차 랭킹, concurrently).
- 복합: (version, level), parent_code.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- hs_nodes ----
    op.create_table(
        "hs_nodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("parent_code", sa.String(10), nullable=True),
        sa.Column("title_ko", sa.Text(), nullable=False),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("notes_excerpt", sa.Text(), nullable=False),
        sa.Column("inclusion_keywords", JSONB(), nullable=True),
        sa.Column("exclusion_keywords", JSONB(), nullable=True),
        sa.Column("essential_character", sa.String(50), nullable=True),
        sa.Column("processing_stage", sa.String(30), nullable=True),
        sa.Column("source_reference", sa.String(200), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("embedding_model", sa.String(50), nullable=True),
        sa.Column("embedding_version", sa.Integer(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("version", "code", name="uq_hs_node_version_code"),
    )
    op.create_index("idx_hs_node_version_level", "hs_nodes", ["version", "level"])
    op.create_index("idx_hs_node_parent", "hs_nodes", ["parent_code"])
    # GIN — inclusion/exclusion keywords substring 룩업.
    op.execute(
        "CREATE INDEX idx_hs_node_inclusion_gin ON hs_nodes USING gin (inclusion_keywords jsonb_path_ops)"
    )
    op.execute(
        "CREATE INDEX idx_hs_node_exclusion_gin ON hs_nodes USING gin (exclusion_keywords jsonb_path_ops)"
    )
    # ivfflat — 2차 랭킹. lists=100 (부·류 수준엔 과함이지만 호·소호 확장 대비).
    op.execute(
        "CREATE INDEX idx_hs_node_embedding ON hs_nodes "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # ---- hs_sibling_discriminators ----
    op.create_table(
        "hs_sibling_discriminators",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("hs_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vs_code", sa.String(10), nullable=False),
        sa.Column("discriminator", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(200), nullable=True),
    )
    op.create_index("idx_sibling_disc_node", "hs_sibling_discriminators", ["node_id"])

    # ---- hs_common_errors ----
    op.create_table(
        "hs_common_errors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("hs_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mistake_pattern", sa.Text(), nullable=False),
        sa.Column("correct_path", sa.Text(), nullable=False),
        sa.Column("source", sa.String(100), nullable=True),
    )
    op.create_index("idx_common_err_node", "hs_common_errors", ["node_id"])

    # ---- hs_precedents ----
    # 법무 요구: status CHECK 제약으로 '참고만'|'적용불가' 만 허용 → 자동 적용 금지.
    op.create_table(
        "hs_precedents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("precedent_type", sa.String(30), nullable=False),
        sa.Column("binding_level", sa.Integer(), nullable=False),
        sa.Column("case_no", sa.String(100), nullable=False),
        sa.Column("decided_at", sa.Date(), nullable=True),
        sa.Column("hs_code", sa.String(10), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("full_text_ref", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'참고만'"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("embedding_model", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("precedent_type", "case_no", name="uq_precedent_type_case"),
        sa.CheckConstraint(
            "status IN ('참고만', '적용불가')", name="ck_precedent_status"
        ),
        sa.CheckConstraint(
            "binding_level BETWEEN 1 AND 4", name="ck_precedent_binding_level"
        ),
        sa.CheckConstraint(
            "precedent_type IN ('supreme_court', 'tax_tribunal', 'advance_ruling', 'similar_case')",
            name="ck_precedent_type",
        ),
    )
    op.create_index("idx_precedent_hscode", "hs_precedents", ["hs_code", "binding_level"])
    op.execute(
        "CREATE INDEX idx_precedent_embedding ON hs_precedents "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50)"
    )


def downgrade() -> None:
    op.drop_table("hs_precedents")
    op.drop_table("hs_common_errors")
    op.drop_table("hs_sibling_discriminators")
    op.drop_table("hs_nodes")
