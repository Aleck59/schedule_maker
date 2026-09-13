"""Учебный период

Revision ID: 0006_academic_session
Revises: 0005_specialities_holidays
Create Date: 2026-09-13 18:22:53.564621
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0006_academic_session"
down_revision: str | None = "0005_specialities_holidays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Границы осеннего и весеннего семестра по умолчанию — те же, что в
#: services/sessions.py. Дублируются нарочно: миграция должна работать
#: и через год, когда код успеет поменяться.
AUTUMN = ((9, 1), (12, 31))
SPRING = ((2, 9), (6, 30))


def _existing_data(connection) -> bool:
    """Есть ли в базе нагрузка или версии расписания."""
    for table in ("lesson_demand", "schedule_version"):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            return True
    return False


def _adopt_existing(connection) -> None:
    """Перенести имеющиеся данные в текущий учебный период.

    Без этого шага после обновления нагрузка осталась бы без периода и
    пропала бы из списка: программа показывает только то, что относится
    к текущему периоду. Пустую базу трогать незачем — период заведётся
    сам при первом обращении.
    """
    if not _existing_data(connection):
        return

    today = date.today()
    autumn = today.month >= 8 or today.month == 1
    year_start = today.year if today.month >= 8 else today.year - 1
    span = AUTUMN if autumn else SPRING
    starts_on = date(year_start if autumn else year_start + 1, *span[0])
    ends_on = date(year_start if autumn else year_start + 1, *span[1])

    connection.execute(
        sa.text(
            "INSERT INTO academic_session "
            "(year_start, term, starts_on, ends_on, weeks, is_current, is_archived, note,"
            " created_at, updated_at) "
            "VALUES (:year, :term, :starts, :ends, 17, 1, 0, :note, :now, :now)"
        ),
        {
            "year": year_start,
            "term": "autumn" if autumn else "spring",
            "starts": starts_on,
            "ends": ends_on,
            "note": "Заведён при обновлении: сюда перенесены прежние данные.",
            "now": datetime.now(UTC),
        },
    )
    session_id = connection.execute(
        sa.text("SELECT id FROM academic_session WHERE is_current = 1")
    ).scalar_one()

    for table in ("lesson_demand", "schedule_version", "curriculum"):
        connection.execute(
            sa.text(f"UPDATE {table} SET session_id = :sid WHERE session_id IS NULL"),
            {"sid": session_id},
        )


def upgrade() -> None:
    op.create_table(
        "academic_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("year_start", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(length=20), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("weeks", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ends_on >= starts_on", name=op.f("ck_academic_session_session_dates")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academic_session")),
        sa.UniqueConstraint("year_start", "term", name="uq_session_year_term"),
    )
    with op.batch_alter_table("academic_session", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_academic_session_is_current"), ["is_current"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_academic_session_year_start"), ["year_start"], unique=False
        )

    with op.batch_alter_table("curriculum", schema=None) as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_curriculum_session_id"), ["session_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_curriculum_session_id_academic_session"),
            "academic_session",
            ["session_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("lesson_demand", schema=None) as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_lesson_demand_session_id"), ["session_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_lesson_demand_session_id_academic_session"),
            "academic_session",
            ["session_id"],
            ["id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("schedule_version", schema=None) as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_schedule_version_session_id"), ["session_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_schedule_version_session_id_academic_session"),
            "academic_session",
            ["session_id"],
            ["id"],
            ondelete="CASCADE",
        )

    _adopt_existing(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("schedule_version", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_schedule_version_session_id_academic_session"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_schedule_version_session_id"))
        batch_op.drop_column("session_id")

    with op.batch_alter_table("lesson_demand", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_lesson_demand_session_id_academic_session"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_lesson_demand_session_id"))
        batch_op.drop_column("session_id")

    with op.batch_alter_table("curriculum", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_curriculum_session_id_academic_session"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_curriculum_session_id"))
        batch_op.drop_column("session_id")

    with op.batch_alter_table("academic_session", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_academic_session_year_start"))
        batch_op.drop_index(batch_op.f("ix_academic_session_is_current"))

    op.drop_table("academic_session")
