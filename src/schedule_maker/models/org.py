"""Организационные справочники: кампусы, аудитории, факультеты, дисциплины, звонки."""

from __future__ import annotations

from datetime import time

from sqlalchemy import Boolean, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.enums import RoomKind, StudyForm
from schedule_maker.models.base import Base, TimestampMixin


class Campus(Base, TimestampMixin):
    """Город или филиал: Махачкала, Кизляр."""

    __tablename__ = "campus"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    address: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    rooms: Mapped[list[Room]] = relationship(back_populates="campus", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<Campus {self.name}>"


class CampusTravel(Base):
    """Время на переезд между кампусами, в минутах.

    Нужно, чтобы запретить пары в Махачкале и Кизляре в соседних слотах.
    """

    __tablename__ = "campus_travel"
    __table_args__ = (UniqueConstraint("from_campus_id", "to_campus_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    from_campus_id: Mapped[int] = mapped_column(ForeignKey("campus.id", ondelete="CASCADE"))
    to_campus_id: Mapped[int] = mapped_column(ForeignKey("campus.id", ondelete="CASCADE"))
    minutes: Mapped[int] = mapped_column(Integer, default=0)


class Room(Base, TimestampMixin):
    """Аудитория."""

    __tablename__ = "room"
    __table_args__ = (UniqueConstraint("campus_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campus_id: Mapped[int] = mapped_column(ForeignKey("campus.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(160), default="")
    kind: Mapped[str] = mapped_column(String(30), default=RoomKind.SEMINAR)
    capacity: Mapped[int] = mapped_column(Integer, default=30)
    equipment: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    campus: Mapped[Campus] = relationship(back_populates="rooms")

    @property
    def label(self) -> str:
        return f"{self.code} ({self.campus.name})" if self.campus else self.code


class Faculty(Base, TimestampMixin):
    """Направление / факультет: Юриспруденция, Экономика, Биология."""

    __tablename__ = "faculty"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    short: Mapped[str] = mapped_column(String(40), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subject(Base, TimestampMixin):
    """Дисциплина."""

    __tablename__ = "subject"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    short: Mapped[str] = mapped_column(String(40), default="")
    color: Mapped[str] = mapped_column(String(20), default="blue")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def display(self) -> str:
        return self.short or self.name


class BellSlot(Base):
    """Сетка звонков: номер пары -> время начала и конца.

    Своя на каждый кампус и форму обучения: у вечерников пары начинаются позже.
    Отсюда берётся смысл требования «строго с 16:00».
    """

    __tablename__ = "bell_slot"
    __table_args__ = (UniqueConstraint("campus_id", "study_form", "slot_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campus_id: Mapped[int] = mapped_column(ForeignKey("campus.id", ondelete="CASCADE"), index=True)
    study_form: Mapped[str] = mapped_column(String(20), default=StudyForm.FULL_TIME)
    slot_index: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[time] = mapped_column(Time)
    ends_at: Mapped[time] = mapped_column(Time)

    @property
    def label(self) -> str:
        return f"{self.slot_index + 1} пара · {self.starts_at:%H:%M}–{self.ends_at:%H:%M}"
