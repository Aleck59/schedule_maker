"""Таблицы базы данных.

Ключевое разделение — `Lesson` и `Placement`: первое говорит, *сколько* пар
дисциплины нужно поставить, второе — *когда и где* стоит конкретная пара.
Генерация создаёт назначения, ручная правка их двигает, а флаг `pinned`
защищает утверждённое от следующей перегенерации.

Названия классов повторяют доменные модели, но это разные вещи: здесь строки
таблиц, там — данные для расчёта. Перевод между ними живёт в `repo.py`.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Location(Base):
    __tablename__ = "location"
    id: Mapped[int] = mapped_column(primary_key=True)
    city: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(120), default="")

    rooms: Mapped[list[Room]] = relationship(back_populates="location", cascade="all, delete")


class Room(Base):
    __tablename__ = "room"
    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    capacity: Mapped[int] = mapped_column(Integer, default=30)
    kind: Mapped[str] = mapped_column(String(20), default="practice")
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)

    location: Mapped[Location] = relationship(back_populates="rooms")


class StudentGroup(Base):
    """Группа или её подгруппа: подгруппа — та же запись с заполненным `parent_id`."""

    __tablename__ = "student_group"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    course: Mapped[int] = mapped_column(Integer, default=1)
    program: Mapped[str] = mapped_column(String(120), default="")
    study_form: Mapped[str] = mapped_column(String(20), default="full_time")
    #: Можно ли дробить группу на подгруппы. У биологов в Махачкале — нельзя.
    split_flag: Mapped[bool] = mapped_column(Boolean, default=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id", ondelete="CASCADE"))
    headcount: Mapped[int] = mapped_column(Integer, default=25)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_group.id", ondelete="CASCADE"), nullable=True
    )
    max_pairs_per_day: Mapped[int] = mapped_column(Integer, default=4)


class Teacher(Base):
    __tablename__ = "teacher"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(120), default="")
    teaching_mode: Mapped[str] = mapped_column(String(20), default="offline")
    frequency: Mapped[str] = mapped_column(String(20), default="weekly")
    #: Сколько дней подряд должен вести приезжающий преподаватель.
    block_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Белый список дней недели; пусто — любые рабочие дни.
    allowed_days: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    #: Чёрный список дней недели.
    forbidden_days: Mapped[list[int]] = mapped_column(JSON, default=list)
    #: Название внешнего расписания, от которого зависит преподаватель.
    external_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    max_pairs_per_day: Mapped[int] = mapped_column(Integer, default=4)


class Discipline(Base):
    __tablename__ = "discipline"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(120), default="")


class Lesson(Base):
    """Требование учебного плана: сколько пар кому и с кем нужно."""

    __tablename__ = "lesson"
    id: Mapped[int] = mapped_column(primary_key=True)
    discipline_id: Mapped[int] = mapped_column(ForeignKey("discipline.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(ForeignKey("student_group.id", ondelete="CASCADE"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id", ondelete="CASCADE"))
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id", ondelete="CASCADE"))
    lesson_type: Mapped[str] = mapped_column(String(20), default="practice")
    pairs_total: Mapped[int] = mapped_column(Integer, default=1)
    max_per_day: Mapped[int] = mapped_column(Integer, default=2)
    required_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    required_room_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    required_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL"), nullable=True
    )
    preferred_parity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)


class PeriodTemplate(Base):
    """Расписание звонков филиала: номер пары → время начала и конца."""

    __tablename__ = "period_template"
    __table_args__ = (UniqueConstraint("location_id", "period", name="uq_period_per_location"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id", ondelete="CASCADE"))
    period: Mapped[int] = mapped_column(Integer)
    start: Mapped[time] = mapped_column(Time)
    end: Mapped[time] = mapped_column(Time)


class ExternalBusy(Base):
    """Слот, занятый чужим расписанием (например, колледжем)."""

    __tablename__ = "external_busy"
    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id", ondelete="CASCADE"))
    day: Mapped[int] = mapped_column(Integer)
    period: Mapped[int] = mapped_column(Integer)
    parity: Mapped[str] = mapped_column(String(10), default="every")
    source: Mapped[str] = mapped_column(String(120), default="")


class Schedule(Base):
    """Одно расписание: семестр, филиал, состояние.

    Публично видны только расписания со статусом `published` — на этом и стоит
    разделение доступа: черновик правит администратор, опубликованное смотрят
    студенты.
    """

    __tablename__ = "schedule"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    semester: Mapped[str] = mapped_column(String(60), default="")
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published
    days: Mapped[int] = mapped_column(Integer, default=6)
    periods: Mapped[int] = mapped_column(Integer, default=7)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    placements: Mapped[list[Placement]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class Placement(Base):
    """Конкретная пара: когда и где."""

    __tablename__ = "placement"
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedule.id", ondelete="CASCADE"))
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lesson.id", ondelete="CASCADE"))
    day: Mapped[int] = mapped_column(Integer)
    period: Mapped[int] = mapped_column(Integer)
    parity: Mapped[str] = mapped_column(String(10), default="every")
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL"), nullable=True
    )
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Закреплено вручную — перегенерация такую пару не двигает.
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    schedule: Mapped[Schedule] = relationship(back_populates="placements")


class Substitution(Base):
    """Замена или перенос на конкретную дату — поверх основного расписания."""

    __tablename__ = "substitution"
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedule.id", ondelete="CASCADE"))
    on_date: Mapped[date] = mapped_column(Date)
    placement_id: Mapped[int] = mapped_column(ForeignKey("placement.id", ondelete="CASCADE"))
    new_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL"), nullable=True
    )
    new_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL"), nullable=True
    )
    new_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(300), default="")


class ConstraintConfig(Base):
    """Настройка правила для конкретного расписания.

    Благодаря этой таблице набор действующих правил является данными: включить
    правило, смягчить его или поменять вес можно из интерфейса, не трогая код.
    """

    __tablename__ = "constraint_config"
    __table_args__ = (
        UniqueConstraint("schedule_id", "constraint_id", name="uq_constraint_per_schedule"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedule.id", ondelete="CASCADE"))
    constraint_id: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: None — использовать жёсткость, заданную самим правилом.
    hard: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AdminUser(Base):
    """Учётная запись администратора — единственный вход с паролем."""

    __tablename__ = "admin_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    salt: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
