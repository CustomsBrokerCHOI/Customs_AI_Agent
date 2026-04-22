"""classify_jobs.min_confidence_pct 컬럼 추가

관세사가 분류 요청마다 후보 제시 최소 신뢰도를 31~69 (%) 범위로 지정할 수 있게
한다. NULL 이면 엔진 기본값(30%).

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "classify_jobs",
        sa.Column("min_confidence_pct", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("classify_jobs", "min_confidence_pct")
