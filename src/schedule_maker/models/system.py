"""Системные таблицы: плагины, журнал, внешние источники расписания."""

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

from schedule_maker.enums import WeekParity
from schedule_maker.models.base import Base, TimestampMixin


class PluginState(Base, TimestampMixin):
    """Включён ли плагин и его настройки. Правится на /admin/plugins."""

    __tablename__ = "plugin_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_key: Mapped[str] = mapped_column(String(120), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class ExternalSource(Base, TimestampMixin):
    """Внешнее расписание — например «Колледж».

    ``plugin_key`` указывает, какой DataSource-плагин умеет его читать.
    """

    __tablename__ = "external_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    plugin_key: Mapped[str] = mapped_column(String(120), default="source.ics_url")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_message: Mapped[str] = mapped_column(String(500), default="")

    busy_slots: Mapped[list[ExternalBusy]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class ExternalBusy(Base):
    """Кэш занятости преподавателя во внешнем учреждении."""

    __tablename__ = "external_busy"
    __table_args__ = (Index("ix_external_busy_teacher_slot", "teacher_id", "day_of_week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("external_source.id", ondelete="CASCADE"), index=True
    )
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer)
    slot_index: Mapped[int] = mapped_column(Integer)
    week_parity: Mapped[str] = mapped_column(String(10), default=WeekParity.ANY)
    description: Mapped[str] = mapped_column(String(255), default="")

    source: Mapped[ExternalSource] = relationship(back_populates="busy_slots")


class AuditLog(Base):
    """Журнал изменений: кто, что и когда поменял."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    user_login: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
