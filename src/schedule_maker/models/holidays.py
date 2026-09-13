"""Справочник праздников и переносов.

Праздники известны заранее и повторяются каждый год, поэтому держать их
в списке помех неправильно: помеха — это событие, случившееся с
расписанием, а праздник существует сам по себе, даже когда расписания
ещё нет.

Отдельная таблица нужна ещё и потому, что в России праздники ходят
парами: выходной день переносится на рабочий. «2 мая — выходной, а
суббота 4-го — рабочая» описывается двумя строками, и обе нужны.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.models.base import Base, TimestampMixin
from schedule_maker.models.org import Campus


class Holiday(Base, TimestampMixin):
    """Один день, в который учебный процесс идёт не как обычно."""

    __tablename__ = "holiday"
    __table_args__ = (UniqueConstraint("on_date", "campus_id", name="uq_holiday_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    on_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(200))

    #: Рабочая суббота взамен праздника. Такой день не отменяет занятия,
    #: а наоборот — в него учатся, хотя по календарю он выходной.
    is_working: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Праздник может быть местным: День города в одном филиале и
    #: обычный вторник в другом.
    campus_id: Mapped[int | None] = mapped_column(
        ForeignKey("campus.id", ondelete="CASCADE"), nullable=True, index=True
    )

    #: Год, к которому относится запись. Хранится отдельно от даты, чтобы
    #: отбирать список по учебному году одним условием.
    year: Mapped[int] = mapped_column(Integer, index=True, default=0)

    note: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    campus: Mapped[Campus | None] = relationship(lazy="joined")

    @property
    def kind_label(self) -> str:
        return "рабочий день" if self.is_working else "выходной"

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<Holiday {self.on_date} {self.title}>"
