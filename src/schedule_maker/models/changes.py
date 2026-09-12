"""Экстренные изменения: то, что ломает расписание на конкретную дату.

Постоянное расписание — это шаблон недели. Жизнь ломает его точечно:
праздник, больничный, ремонт в аудитории. Чинить ради этого сам шаблон
нельзя — через неделю всё вернётся на место, а история правок потеряется.

Поэтому изменения живут отдельным слоем поверх расписания:

* ``Disruption`` — причина: «Соколова болеет с 14 по 18 октября».
  Это то, что диспетчер знает и вводит.
* ``ScheduleChange`` — следствие для одного занятия на одну дату:
  отменено, перенесено, ведёт другой преподаватель.

Программа сама находит, какие занятия задевает причина, и предлагает по
ним решения. Диспетчер выбирает, что с каждым делать.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.enums import ChangeKind, DisruptionKind
from schedule_maker.models.academic import StudentGroup
from schedule_maker.models.base import Base, TimestampMixin
from schedule_maker.models.org import Campus, Room
from schedule_maker.models.people import Teacher
from schedule_maker.models.schedule import Assignment


class Disruption(Base, TimestampMixin):
    """Помеха: что случилось, кого касается и в какие дни."""

    __tablename__ = "disruption"
    __table_args__ = (CheckConstraint("date_to >= date_from", name="disruption_dates"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default=DisruptionKind.OTHER)
    title: Mapped[str] = mapped_column(String(200))

    date_from: Mapped[date] = mapped_column(Date, index=True)
    #: Однодневная помеха — это ``date_to == date_from``. Отдельного
    #: признака «один день» нет: он породил бы две ветки в каждом запросе.
    date_to: Mapped[date] = mapped_column(Date, index=True)

    #: Кого касается. Пусто во всех трёх — значит всех: так задаётся праздник.
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), nullable=True, index=True
    )
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_group.id", ondelete="CASCADE"), nullable=True, index=True
    )
    campus_id: Mapped[int | None] = mapped_column(
        ForeignKey("campus.id", ondelete="CASCADE"), nullable=True, index=True
    )

    note: Mapped[str] = mapped_column(String(1000), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    teacher: Mapped[Teacher | None] = relationship(lazy="joined")
    room: Mapped[Room | None] = relationship(lazy="joined")
    group: Mapped[StudentGroup | None] = relationship(lazy="joined")
    campus: Mapped[Campus | None] = relationship(lazy="joined")
    changes: Mapped[list[ScheduleChange]] = relationship(
        back_populates="disruption", cascade="all, delete-orphan"
    )

    @property
    def one_day(self) -> bool:
        return self.date_from == self.date_to

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    @property
    def target_label(self) -> str:
        """Кого касается — одной строкой для списка."""
        if self.teacher:
            return self.teacher.short_name
        if self.room:
            return f"аудитория {self.room.name}"
        if self.group:
            return self.group.name
        if self.campus:
            return self.campus.name
        return "всех"

    def covers(self, day: date) -> bool:
        return self.date_from <= day <= self.date_to

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<Disruption {self.title} {self.date_from}..{self.date_to}>"


class ScheduleChange(Base, TimestampMixin):
    """Что стало с одним занятием в один конкретный день.

    Ссылка на ``assignment`` — на пару в постоянном расписании. Если
    занятие дополнительное и в шаблоне его нет, ссылка пустая, а всё
    нужное записано в полях «нового» времени.
    """

    __tablename__ = "schedule_change"
    __table_args__ = (
        CheckConstraint(
            "assignment_id IS NOT NULL OR (new_day_of_week IS NOT NULL "
            "AND new_slot_index IS NOT NULL)",
            name="change_has_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    on_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20), default=ChangeKind.CANCEL)

    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignment.id", ondelete="CASCADE"), nullable=True, index=True
    )
    disruption_id: Mapped[int | None] = mapped_column(
        ForeignKey("disruption.id", ondelete="SET NULL"), nullable=True, index=True
    )

    #: Куда перенесли. Заполняется только при переносе и при добавлении
    #: занятия, которого нет в постоянном расписании.
    new_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    new_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL"), nullable=True
    )
    new_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL"), nullable=True
    )

    #: Пояснение для студентов: «пара перенесена на субботу, 2-я пара».
    #: Хранится текстом, а не собирается при показе: причина могла быть
    #: любой, и переписывать её потом задним числом неправильно.
    note: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    assignment: Mapped[Assignment | None] = relationship(lazy="joined")
    disruption: Mapped[Disruption | None] = relationship(back_populates="changes")
    new_room: Mapped[Room | None] = relationship(lazy="joined", foreign_keys=[new_room_id])
    new_teacher: Mapped[Teacher | None] = relationship(lazy="joined", foreign_keys=[new_teacher_id])

    @property
    def cancelled(self) -> bool:
        return self.kind == ChangeKind.CANCEL

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<ScheduleChange {self.on_date} {self.kind}>"
