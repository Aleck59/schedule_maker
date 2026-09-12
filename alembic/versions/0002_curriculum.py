"""Учебный план и учёт часов

Revision ID: 0002_curriculum
Revises: 0001_initial
Create Date: 2026-09-12 03:32:25.149699
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_curriculum"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "curriculum",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("speciality_code", sa.String(length=40), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("study_form", sa.String(length=20), nullable=False),
        sa.Column("weeks_per_semester", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "weeks_per_semester BETWEEN 1 AND 52", name=op.f("ck_curriculum_weeks_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_curriculum")),
    )
    op.create_table(
        "curriculum_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("curriculum_id", sa.Integer(), nullable=False),
        sa.Column("index_code", sa.String(length=40), nullable=False),
        sa.Column("raw_name", sa.String(length=300), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("teacher_id", sa.Integer(), nullable=True),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column("lecture_hours", sa.Integer(), nullable=False),
        sa.Column("practice_hours", sa.Integer(), nullable=False),
        sa.Column("lab_hours", sa.Integer(), nullable=False),
        sa.Column("consult_hours", sa.Integer(), nullable=False),
        sa.Column("self_hours", sa.Integer(), nullable=False),
        sa.Column("attest_hours", sa.Integer(), nullable=False),
        sa.Column("total_hours", sa.Integer(), nullable=False),
        sa.Column("control_form", sa.String(length=20), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "semester BETWEEN 1 AND 12", name=op.f("ck_curriculum_item_semester_range")
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_id"],
            ["curriculum.id"],
            name=op.f("fk_curriculum_item_curriculum_id_curriculum"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["student_group.id"],
            name=op.f("fk_curriculum_item_group_id_student_group"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subject.id"],
            name=op.f("fk_curriculum_item_subject_id_subject"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teacher.id"],
            name=op.f("fk_curriculum_item_teacher_id_teacher"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_curriculum_item")),
        sa.UniqueConstraint("curriculum_id", "index_code", "semester", name="uq_item_in_plan"),
    )
    with op.batch_alter_table("curriculum_item", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_curriculum_item_curriculum_id"), ["curriculum_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_curriculum_item_group_id"), ["group_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_curriculum_item_semester"), ["semester"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_curriculum_item_subject_id"), ["subject_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_curriculum_item_teacher_id"), ["teacher_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("curriculum_item", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_curriculum_item_teacher_id"))
        batch_op.drop_index(batch_op.f("ix_curriculum_item_subject_id"))
        batch_op.drop_index(batch_op.f("ix_curriculum_item_semester"))
        batch_op.drop_index(batch_op.f("ix_curriculum_item_group_id"))
        batch_op.drop_index(batch_op.f("ix_curriculum_item_curriculum_id"))

    op.drop_table("curriculum_item")
    op.drop_table("curriculum")
