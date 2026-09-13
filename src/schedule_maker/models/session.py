"""Учебный год и семестр.

Раньше семестр был строкой в названии версии расписания, а нагрузка —
общей. Пока семестр один, это незаметно; как только рядом появляется
второй, они накладываются: в справочнике нагрузки лежат вперемешку
осенние и весенние занятия, и понять, какие из них действующие, нельзя.

Учебный период ограничивает собой то, что меняется от семестра к
семестру: нагрузку и версии расписания. Справочники — аудитории,
преподаватели, дисциплины — живут дольше и периоду не принадлежат:
аудитория 305 не перестаёт существовать в январе.

Даты начала и конца нужны не для красоты. По ним считается чётность
недели: первая учебная неделя нечётная по определению, и это гораздо
надёжнее, чем привязка к номеру недели в году, где январь произвольно
сдвигает счёт.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import Boolean, CheckConstraint, Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from schedule_maker.enums import Term, WeekParity
from schedule_maker.models.base import Base, TimestampMixin


class AcademicSession(Base, TimestampMixin):
    """Учебный период: год плюс семестр."""

    __tablename__ = "academic_session"
    __table_args__ = (
        UniqueConstraint("year_start", "term", name="uq_session_year_term"),
        CheckConstraint("ends_on >= starts_on", name="session_dates"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: Год начала учебного года: 2026 для «2026/2027».
    year_start: Mapped[int] = mapped_column(Integer, index=True)
    term: Mapped[str] = mapped_column(String(20), default=Term.AUTUMN)

    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)

    #: Сколько учебных недель в периоде. Считается по датам, но хранится
    #: отдельно: каникулы и практики выпадают из счёта, и человек правит
    #: это число руками.
    weeks: Mapped[int] = mapped_column(Integer, default=17)

    #: Период, с которым сейчас работают. Ровно один на всю базу:
    #: за этим следит ``make_current``.
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    note: Mapped[str] = mapped_column(String(500), default="")

    @property
    def year_label(self) -> str:
        return f"{self.year_start}/{self.year_start + 1}"

    @property
    def title(self) -> str:
        from schedule_maker.enums import TERM_LABELS

        return f"{self.year_label}, {TERM_LABELS.get(self.term, self.term)}"

    @property
    def contains_today(self) -> bool:
        return self.starts_on <= date.today() <= self.ends_on

    def week_number(self, day: date) -> int:
        """Номер учебной недели для даты. Первая неделя — первая.

        До начала периода возвращается ноль: спрашивать номер недели у
        даты, которая в период не входит, бессмысленно, и выдавать
        отрицательное число было бы хуже, чем честный ноль.
        """
        if day < self.starts_on:
            return 0
        # Неделя начинается с понедельника, поэтому считаем от
        # понедельника первой учебной недели, а не от самой даты начала:
        # иначе семестр, начавшийся в среду, дал бы вторую неделю уже
        # в ближайший понедельник.
        first_monday = self.starts_on - timedelta(days=self.starts_on.weekday())
        return (day - first_monday).days // 7 + 1

    def parity_of(self, day: date) -> str:
        """Чётность учебной недели: первая неделя нечётная."""
        number = self.week_number(day)
        if number <= 0:
            return WeekParity.ANY
        return WeekParity.ODD if number % 2 == 1 else WeekParity.EVEN

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<AcademicSession {self.title}>"
