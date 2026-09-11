"""Версии расписания, расстановка и экземпляры ограничений."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.enums import ConstraintScope, RunStatus, VersionStatus, WeekParity
from schedule_maker.models.academic import LessonDemand
from schedule_maker.models.base import Base, TimestampMixin
from schedule_maker.models.org import Room


class ScheduleVersion(Base, TimestampMixin):
    """Версия расписания. Публичный раздел показывает только ``published``."""

    __tablename__ = "schedule_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    semester: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(20), default=VersionStatus.DRAFT, index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    hard_score: Mapped[int] = mapped_column(Integer, default=0)
    soft_score: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )

    @property
    def is_published(self) -> bool:
        return self.status == VersionStatus.PUBLISHED


class Assignment(Base):
    """Конкретная пара в сетке."""

    __tablename__ = "assignment"
    __table_args__ = (
        Index("ix_assignment_version_slot", "version_id", "day_of_week", "slot_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )
    demand_id: Mapped[int] = mapped_column(
        ForeignKey("lesson_demand.id", ondelete="CASCADE"), index=True
    )
    component_index: Mapped[int] = mapped_column(Integer, default=0)

    day_of_week: Mapped[int] = mapped_column(Integer)
    slot_index: Mapped[int] = mapped_column(Integer)
    week_parity: Mapped[str] = mapped_column(String(10), default=WeekParity.ANY)
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Замок в стиле Untis: генератор обязан оставить пару на месте.
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(255), default="")

    version: Mapped[ScheduleVersion] = relationship(back_populates="assignments")
    demand: Mapped[LessonDemand] = relationship(lazy="joined")
    room: Mapped[Room | None] = relationship(lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<Assignment d{self.day_of_week} s{self.slot_index} demand={self.demand_id}>"


class ConstraintRule(Base, TimestampMixin):
    """Экземпляр ограничения.

    Тип правила задаёт плагин (``plugin_key``), параметры валидируются его
    ``params_schema``. Новое правило = новый плагин, миграция БД не нужна.
    """

    __tablename__ = "constraint_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_key: Mapped[str] = mapped_column(String(120), index=True)
    scope_type: Mapped[str] = mapped_column(String(20), default=ConstraintScope.GLOBAL)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # Вес 0..100 в духе FET: 100 — жёсткое, меньше — мягкое с этим приоритетом.
    weight: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(255), default="")

    @property
    def is_hard(self) -> bool:
        return self.weight >= 100


class GenerationRun(Base, TimestampMixin):
    """Запуск генератора — для показа прогресса и истории."""

    __tablename__ = "generation_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )
    solver_key: Mapped[str] = mapped_column(String(80), default="solver.greedy")
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.PENDING)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(500), default="")
    hard_score: Mapped[int] = mapped_column(Integer, default=0)
    soft_score: Mapped[int] = mapped_column(Integer, default=0)
    placed: Mapped[int] = mapped_column(Integer, default=0)
    unplaced: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
