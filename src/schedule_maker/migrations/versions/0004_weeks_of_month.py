"""Недели месяца у преподавателя

Revision ID: 0004_weeks_of_month
Revises: 0003_changes
Create Date: 2026-09-13 17:48:43.456572
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_weeks_of_month"
down_revision: str | None = "0003_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("teacher_availability", schema=None) as batch_op:
        # server_default обязателен: в таблице уже есть строки, и NOT NULL
        # без значения по умолчанию не даст добавить колонку.
        batch_op.add_column(
            sa.Column(
                "weeks_of_month",
                sa.String(length=20),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("teacher_availability", schema=None) as batch_op:
        batch_op.drop_column("weeks_of_month")
