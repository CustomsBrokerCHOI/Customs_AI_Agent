"""classify_jobs.force_classify 컬럼 추가

Input Gate 후속 질문을 관세사가 "모른다/넘어간다" 로 판단했을 때 엔진이 원본
입력만으로 경합 후보 최대 5건을 반환하도록 하는 요청 플래그. 감사·재현을 위해
요청 단위로 영속 기록.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "classify_jobs",
        sa.Column(
            "force_classify",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("classify_jobs", "force_classify")
