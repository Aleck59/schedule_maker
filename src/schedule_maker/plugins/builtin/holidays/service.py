"""Праздники: справочник и превращение их в отмены занятий."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import DisruptionKind
from schedule_maker.models import Disruption, Holiday

#: Нерабочие праздничные дни по Трудовому кодексу. Даты фиксированные,
#: год подставляется при заполнении. Переносы выходных каждый год свои —
#: их правительство утверждает отдельным постановлением, угадать их
#: нельзя, поэтому они добавляются руками.
FIXED_HOLIDAYS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "Новогодние каникулы"),
    (1, 2, "Новогодние каникулы"),
    (1, 3, "Новогодние каникулы"),
    (1, 4, "Новогодние каникулы"),
    (1, 5, "Новогодние каникулы"),
    (1, 6, "Новогодние каникулы"),
    (1, 7, "Рождество Христово"),
    (1, 8, "Новогодние каникулы"),
    (2, 23, "День защитника Отечества"),
    (3, 8, "Международный женский день"),
    (5, 1, "Праздник Весны и Труда"),
    (5, 9, "День Победы"),
    (6, 12, "День России"),
    (11, 4, "День народного единства"),
)


@dataclass(slots=True)
class FillReport:
    """Что добавилось при заполнении справочника."""

    created: int = 0
    skipped: int = 0


def fill_year(session: Session, year: int, campus_id: int | None = None) -> FillReport:
    """Внести в справочник государственные праздники за год.

    Уже внесённые дни не трогаются: у них мог быть изменён филиал или
    добавлена заметка, и затирать чужую правку нельзя.
    """
    existing = {
        (row.on_date, row.campus_id)
        for row in session.scalars(select(Holiday).where(Holiday.year == year))
    }
    report = FillReport()
    for month, day, title in FIXED_HOLIDAYS:
        when = date(year, month, day)
        if (when, campus_id) in existing:
            report.skipped += 1
            continue
        session.add(
            Holiday(
                on_date=when,
                title=title,
                year=year,
                campus_id=campus_id,
                note="Внесено автоматически по Трудовому кодексу",
            )
        )
        report.created += 1
    session.flush()
    return report


def holidays_of(session: Session, year: int) -> list[Holiday]:
    return list(
        session.scalars(select(Holiday).where(Holiday.year == year).order_by(Holiday.on_date))
    )


def unlinked(session: Session, holidays: list[Holiday]) -> list[Holiday]:
    """Праздники, по которым ещё не созданы отмены занятий.

    Совпадение ищется по дате и филиалу: помеха на 9 мая в Махачкале не
    закрывает тот же день в Кизляре.
    """
    known = {
        (row.date_from, row.campus_id)
        for row in session.scalars(
            select(Disruption).where(Disruption.kind == DisruptionKind.HOLIDAY)
        )
    }
    return [
        holiday
        for holiday in holidays
        if not holiday.is_working and (holiday.on_date, holiday.campus_id) not in known
    ]


def make_disruptions(session: Session, holidays: list[Holiday]) -> list[Disruption]:
    """Превратить выбранные праздники в помехи расписания.

    Дальше с ними работает обычная страница изменений: она сама находит
    задетые занятия и предлагает их отменить.
    """
    created = []
    for holiday in holidays:
        if holiday.is_working:
            continue  # рабочая суббота занятия не отменяет, а добавляет
        disruption = Disruption(
            kind=DisruptionKind.HOLIDAY,
            title=holiday.title,
            date_from=holiday.on_date,
            date_to=holiday.on_date,
            campus_id=holiday.campus_id,
            note="Создано из справочника праздников",
        )
        session.add(disruption)
        created.append(disruption)
    session.flush()
    return created
