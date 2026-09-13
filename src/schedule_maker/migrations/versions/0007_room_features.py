"""Признаки аудитории отдельным справочником

Revision ID: 0007_room_features
Revises: 0006_academic_session
Create Date: 2026-09-13 19:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0007_room_features"
down_revision: str | None = "0006_academic_session"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _split(text: str) -> list[str]:
    """Разобрать старое поле «оборудование» — текст через запятую."""
    return [part.strip() for part in (text or "").replace(";", ",").split(",") if part.strip()]


def upgrade() -> None:
    op.create_table(
        "room_feature_kind",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("short", sa.String(length=40), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_room_feature_kind")),
        sa.UniqueConstraint("name", name=op.f("uq_room_feature_kind_name")),
    )
    op.create_table(
        "room_feature",
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["feature_id"],
            ["room_feature_kind.id"],
            name=op.f("fk_room_feature_feature_id_room_feature_kind"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["room.id"],
            name=op.f("fk_room_feature_room_id_room"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("room_id", "feature_id", name=op.f("pk_room_feature")),
    )
    op.create_table(
        "demand_feature",
        sa.Column("demand_id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["demand_id"],
            ["lesson_demand.id"],
            name=op.f("fk_demand_feature_demand_id_lesson_demand"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["feature_id"],
            ["room_feature_kind.id"],
            name=op.f("fk_demand_feature_feature_id_room_feature_kind"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("demand_id", "feature_id", name=op.f("pk_demand_feature")),
    )

    # Порядок важен. SQLite не умеет ALTER TABLE DROP COLUMN: alembic
    # пересобирает таблицу через временную копию, а DROP TABLE room по
    # дороге уносит с собой строки room_feature — они висят на внешнем
    # ключе с ON DELETE CASCADE. Поэтому старое значение сначала читается
    # в память, потом колонка уходит, и только потом пишутся связи.
    equipment = op.get_bind().execute(sa.text("SELECT id, equipment FROM room")).fetchall()

    with op.batch_alter_table("room", schema=None) as batch_op:
        batch_op.drop_column("equipment")

    _migrate_equipment([(row[0], row[1] or "") for row in equipment])


def _migrate_equipment(rows: list[tuple[int, str]]) -> None:
    """Перенести текст «проектор, компьютеры» в строки справочника.

    Названия сводятся без учёта регистра: «Проектор» и «проектор» в одной
    базе — один и тот же признак, и разводить их по разным строкам значит
    сразу получить справочник с дублями.
    """
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    known: dict[str, int] = {}
    links: set[tuple[int, int]] = set()
    for room_id, equipment in rows:
        for name in _split(equipment):
            key = name.casefold()
            feature_id = known.get(key)
            if feature_id is None:
                bind.execute(
                    sa.text(
                        "INSERT INTO room_feature_kind"
                        " (name, short, note, is_active, created_at, updated_at)"
                        " VALUES (:name, '', '', 1, :now, :now)"
                    ),
                    {"name": name, "now": now},
                )
                feature_id = bind.execute(
                    sa.text("SELECT id FROM room_feature_kind WHERE name = :name"),
                    {"name": name},
                ).scalar_one()
                known[key] = feature_id
            links.add((room_id, feature_id))

    if links:
        bind.execute(
            sa.text("INSERT INTO room_feature (room_id, feature_id) VALUES (:room, :feature)"),
            [{"room": room, "feature": feature} for room, feature in sorted(links)],
        )


def downgrade() -> None:
    # Читаем связи до того, как пересборка room снесёт их каскадом.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT link.room_id, kind.name FROM room_feature AS link"
            " JOIN room_feature_kind AS kind ON kind.id = link.feature_id"
            " ORDER BY link.room_id, kind.name"
        )
    ).fetchall()
    by_room: dict[int, list[str]] = {}
    for room_id, name in rows:
        by_room.setdefault(room_id, []).append(name)

    with op.batch_alter_table("room", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("equipment", sa.String(length=255), nullable=False, server_default="")
        )

    for room_id, names in by_room.items():
        bind.execute(
            sa.text("UPDATE room SET equipment = :text WHERE id = :id"),
            {"text": ", ".join(names), "id": room_id},
        )

    op.drop_table("demand_feature")
    op.drop_table("room_feature")
    op.drop_table("room_feature_kind")
