"""Экстренные изменения расписания

Revision ID: 0003_changes
Revises: 0002_curriculum
Create Date: 2026-09-12 03:54:39.987706
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_changes"
down_revision: str | None = "0002_curriculum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disruption",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=True),
        sa.Column("room_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("campus_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("date_to >= date_from", name=op.f("ck_disruption_disruption_dates")),
        sa.ForeignKeyConstraint(
            ["campus_id"],
            ["campus.id"],
            name=op.f("fk_disruption_campus_id_campus"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["student_group.id"],
            name=op.f("fk_disruption_group_id_student_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["room.id"], name=op.f("fk_disruption_room_id_room"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teacher.id"],
            name=op.f("fk_disruption_teacher_id_teacher"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disruption")),
    )
    with op.batch_alter_table("disruption", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_disruption_campus_id"), ["campus_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_disruption_date_from"), ["date_from"], unique=False)
        batch_op.create_index(batch_op.f("ix_disruption_date_to"), ["date_to"], unique=False)
        batch_op.create_index(batch_op.f("ix_disruption_group_id"), ["group_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_disruption_room_id"), ["room_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_disruption_teacher_id"), ["teacher_id"], unique=False)

    op.create_table(
        "schedule_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=True),
        sa.Column("disruption_id", sa.Integer(), nullable=True),
        sa.Column("new_day_of_week", sa.Integer(), nullable=True),
        sa.Column("new_slot_index", sa.Integer(), nullable=True),
        sa.Column("new_date", sa.Date(), nullable=True),
        sa.Column("new_room_id", sa.Integer(), nullable=True),
        sa.Column("new_teacher_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assignment_id IS NOT NULL OR (new_day_of_week IS NOT NULL "
            "AND new_slot_index IS NOT NULL)",
            name=op.f("ck_schedule_change_change_has_target"),
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["assignment.id"],
            name=op.f("fk_schedule_change_assignment_id_assignment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["disruption_id"],
            ["disruption.id"],
            name=op.f("fk_schedule_change_disruption_id_disruption"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["new_room_id"],
            ["room.id"],
            name=op.f("fk_schedule_change_new_room_id_room"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["new_teacher_id"],
            ["teacher.id"],
            name=op.f("fk_schedule_change_new_teacher_id_teacher"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_change")),
    )
    with op.batch_alter_table("schedule_change", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_schedule_change_assignment_id"), ["assignment_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_schedule_change_disruption_id"), ["disruption_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_schedule_change_on_date"), ["on_date"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("schedule_change", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_schedule_change_on_date"))
        batch_op.drop_index(batch_op.f("ix_schedule_change_disruption_id"))
        batch_op.drop_index(batch_op.f("ix_schedule_change_assignment_id"))

    op.drop_table("schedule_change")
    with op.batch_alter_table("disruption", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_disruption_teacher_id"))
        batch_op.drop_index(batch_op.f("ix_disruption_room_id"))
        batch_op.drop_index(batch_op.f("ix_disruption_group_id"))
        batch_op.drop_index(batch_op.f("ix_disruption_date_to"))
        batch_op.drop_index(batch_op.f("ix_disruption_date_from"))
        batch_op.drop_index(batch_op.f("ix_disruption_campus_id"))

    op.drop_table("disruption")
