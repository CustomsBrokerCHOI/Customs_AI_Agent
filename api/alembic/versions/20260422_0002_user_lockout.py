"""user lockout — failed_login_count + locked_until

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22

계정 무차별 대입 방어. ``MAX_FAILED_LOGINS`` 회 연속 실패 시 ``LOCKOUT_DURATION_SEC``
동안 잠금. 성공하면 카운터 리셋.

- ``failed_login_count`` — 연속 로그인 실패 횟수. 성공 시 0 으로 리셋.
- ``locked_until`` — 잠금 해제 예정 시각 (UTC). NULL 이면 잠금 상태 아님.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
