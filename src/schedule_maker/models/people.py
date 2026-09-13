"""Преподаватели, их доступность и пользователи системы."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.enums import (
    AvailabilityKind,
    DeliveryMode,
    RequestStatus,
    UserRole,
    WeekParity,
)
from schedule_maker.models.base import Base, TimestampMixin
from schedule_maker.models.org import Campus


class Teacher(Base, TimestampMixin):
    """Преподаватель — сущность с наибольшим числом ограничений."""

    __tablename__ = "teacher"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    department: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(160), default="")

    delivery_mode: Mapped[str] = mapped_column(String(10), default=DeliveryMode.OFFLINE)
    base_campus_id: Mapped[int | None] = mapped_column(
        ForeignKey("campus.id", ondelete="SET NULL"), nullable=True
    )
    max_pairs_per_day: Mapped[int] = mapped_column(Integer, default=4)
    max_pairs_per_week: Mapped[int] = mapped_column(Integer, default=24)

    # Внешняя зависимость: расписание берётся из стороннего учреждения (Колледж).
    external_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_source.id", ondelete="SET NULL"), nullable=True
    )
    external_ref: Mapped[str] = mapped_column(String(160), default="")

    note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    base_campus: Mapped[Campus | None] = relationship(lazy="joined")
    availability: Mapped[list[TeacherAvailability]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan"
    )

    @property
    def short_name(self) -> str:
        """«Иванов Иван Иванович» -> «Иванов И. И.»"""
        parts = self.full_name.split()
        if len(parts) < 2:
            return self.full_name
        initials = " ".join(f"{p[0]}." for p in parts[1:3])
        return f"{parts[0]} {initials}"

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<Teacher {self.full_name}>"


class TeacherAvailability(Base):
    """Доступность преподавателя одной таблицей.

    ``kind=allow`` — белый список (White_list). Если у преподавателя есть хотя
    бы одна строка ``allow``, всё, что в неё не попало, считается запрещённым.
    ``kind=deny`` — чёрный список (Black_list), вычитается всегда.

    ``day_of_week=None`` — правило на все дни, ``slot_index=None`` — на весь день.
    """

    __tablename__ = "teacher_availability"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10), default=AvailabilityKind.DENY)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    week_parity: Mapped[str] = mapped_column(String(10), default=WeekParity.ANY)

    #: Недели месяца, в которые правило действует: «1,3» — первая и третья.
    #: Пусто — каждую неделю. Это не то же самое, что чётность: вахтовик,
    #: приезжающий в первую неделю месяца, попадает то на чётную неделю
    #: года, то на нечётную, и парой чёт/нечет его график не описать.
    weeks_of_month: Mapped[str] = mapped_column(String(20), default="")

    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(255), default="")

    teacher: Mapped[Teacher] = relationship(back_populates="availability")

    @property
    def week_numbers(self) -> list[int]:
        """Недели месяца списком чисел. Пустой список — правило на все недели."""
        return sorted(
            {int(part) for part in self.weeks_of_month.split(",") if part.strip().isdigit()}
        )

    def covers_week(self, week_of_month: int) -> bool:
        """Действует ли правило на эту неделю месяца."""
        weeks = self.week_numbers
        return not weeks or week_of_month in weeks


class User(Base, TimestampMixin):
    """Учётная запись. Студентам она не нужна — просмотр открыт."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(80), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.TEACHER)
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL"), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    teacher: Mapped[Teacher | None] = relationship(lazy="joined")

    @property
    def user_role(self) -> UserRole:
        return UserRole(self.role)


class TeacherRequest(Base, TimestampMixin):
    """Пожелание преподавателя из личного кабинета — заявка администратору."""

    __tablename__ = "teacher_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=RequestStatus.NEW)
    answer: Mapped[str] = mapped_column(Text, default="")

    teacher: Mapped[Teacher] = relationship(lazy="joined")
