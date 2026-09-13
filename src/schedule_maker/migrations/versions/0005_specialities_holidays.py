"""Праздники, классификатор специальностей, перевод курса

Revision ID: 0005_specialities_holidays
Revises: 0004_weeks_of_month
Create Date: 2026-09-13 18:02:06.954346
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_specialities_holidays"
down_revision: str | None = "0004_weeks_of_month"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holiday",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("is_working", sa.Boolean(), nullable=False),
        sa.Column("campus_id", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campus_id"],
            ["campus.id"],
            name=op.f("fk_holiday_campus_id_campus"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday")),
        sa.UniqueConstraint("on_date", "campus_id", name="uq_holiday_day"),
    )
    with op.batch_alter_table("holiday", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_holiday_campus_id"), ["campus_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_holiday_on_date"), ["on_date"], unique=False)
        batch_op.create_index(batch_op.f("ix_holiday_year"), ["year"], unique=False)

    op.create_table(
        "speciality",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("short", sa.String(length=60), nullable=False),
        sa.Column("level", sa.String(length=30), nullable=False),
        sa.Column("years", sa.Integer(), nullable=False),
        sa.Column("faculty_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["faculty_id"],
            ["faculty.id"],
            name=op.f("fk_speciality_faculty_id_faculty"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_speciality")),
        sa.UniqueConstraint("code", "level", name="uq_speciality_code"),
    )
    with op.batch_alter_table("speciality", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_speciality_code"), ["code"], unique=False)
        batch_op.create_index(batch_op.f("ix_speciality_faculty_id"), ["faculty_id"], unique=False)

    with op.batch_alter_table("student_group", schema=None) as batch_op:
        batch_op.add_column(sa.Column("speciality_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("admission_year", sa.Integer(), nullable=True))
        # В таблице уже есть группы: NOT NULL без значения по умолчанию
        # не даст добавить колонку.
        batch_op.add_column(
            sa.Column("graduated", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index(
            batch_op.f("ix_student_group_speciality_id"), ["speciality_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_student_group_speciality_id_speciality"),
            "speciality",
            ["speciality_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("student_group", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_student_group_speciality_id_speciality"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_student_group_speciality_id"))
        batch_op.drop_column("graduated")
        batch_op.drop_column("admission_year")
        batch_op.drop_column("speciality_id")

    with op.batch_alter_table("speciality", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_speciality_faculty_id"))
        batch_op.drop_index(batch_op.f("ix_speciality_code"))

    op.drop_table("speciality")
    with op.batch_alter_table("holiday", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_holiday_year"))
        batch_op.drop_index(batch_op.f("ix_holiday_on_date"))
        batch_op.drop_index(batch_op.f("ix_holiday_campus_id"))

    op.drop_table("holiday")
