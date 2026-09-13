"""Учебный период: какой сейчас и что ему принадлежит.

Программа всегда работает внутри одного периода. Переключение периода —
это не фильтр в списке, а смена рабочего контекста: меняется нагрузка,
меняются версии расписания, меняется чётность недели.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import Term
from schedule_maker.models import AcademicSession

#: Месяц, с которого начинается осенний семестр, и месяц, с которого —
#: весенний. Используются, только когда период заводится сам: человек
#: потом правит даты под свой календарь.
AUTUMN_START = (9, 1)
AUTUMN_END = (12, 31)
SPRING_START = (2, 9)
SPRING_END = (6, 30)

#: Обычная длина семестра в учебных неделях.
DEFAULT_WEEKS = 17


def term_of(day: date) -> str:
    """Какой семестр идёт в этот день.

    Январь относится к осеннему семестру: сессия — это его хвост, а не
    начало весеннего.
    """
    return Term.AUTUMN if day.month >= 8 or day.month == 1 else Term.SPRING


def year_start_of(day: date) -> int:
    """Год начала учебного года, которому принадлежит дата."""
    return day.year if day.month >= 8 else day.year - 1


def default_dates(year_start: int, term: str) -> tuple[date, date]:
    """Границы периода по умолчанию."""
    if term == Term.AUTUMN:
        return (
            date(year_start, *AUTUMN_START),
            date(year_start, *AUTUMN_END),
        )
    return (
        date(year_start + 1, *SPRING_START),
        date(year_start + 1, *SPRING_END),
    )


def list_sessions(session: Session, *, with_archived: bool = False) -> list[AcademicSession]:
    query = select(AcademicSession).order_by(
        AcademicSession.year_start.desc(), AcademicSession.term
    )
    if not with_archived:
        query = query.where(AcademicSession.is_archived.is_(False))
    return list(session.scalars(query))


def current_session(session: Session) -> AcademicSession:
    """Период, с которым сейчас работают.

    Если ни один не отмечен текущим, берётся тот, в который попадает
    сегодняшний день; если и такого нет — создаётся. Программа не должна
    останавливаться из-за того, что период забыли завести.
    """
    marked = session.scalars(
        select(AcademicSession).where(AcademicSession.is_current.is_(True))
    ).first()
    if marked is not None:
        return marked

    today = date.today()
    for item in list_sessions(session, with_archived=True):
        if item.contains_today:
            return make_current(session, item)

    return make_current(session, ensure_session(session, year_start_of(today), term_of(today)))


def ensure_session(session: Session, year_start: int, term: str) -> AcademicSession:
    """Найти период или завести его с обычными для него датами."""
    found = session.scalars(
        select(AcademicSession).where(
            AcademicSession.year_start == year_start, AcademicSession.term == term
        )
    ).first()
    if found is not None:
        return found

    starts_on, ends_on = default_dates(year_start, term)
    created = AcademicSession(
        year_start=year_start,
        term=term,
        starts_on=starts_on,
        ends_on=ends_on,
        weeks=DEFAULT_WEEKS,
    )
    session.add(created)
    session.flush()
    return created


def make_current(session: Session, target: AcademicSession) -> AcademicSession:
    """Сделать период текущим. Текущий ровно один — за этим следим здесь."""
    for item in session.scalars(
        select(AcademicSession).where(AcademicSession.is_current.is_(True))
    ):
        item.is_current = False
    target.is_current = True
    target.is_archived = False
    session.flush()
    return target


def next_session(current: AcademicSession) -> tuple[int, str]:
    """Какой период идёт следующим за этим."""
    if current.term == Term.AUTUMN:
        return current.year_start, Term.SPRING
    return current.year_start + 1, Term.AUTUMN


def semester_of_group(session_: AcademicSession, admission_year: int | None) -> int | None:
    """Какой по счёту семестр идёт у группы в этом периоде.

    Учебный план считает семестры от первого до восьмого, а календарь —
    годами. Связывает их год набора: группа, поступившая в 2025-м, в
    осеннем семестре 2026/2027 учится в третьем семестре.

    Без года набора ответа нет, и выдумывать его нельзя — вернётся
    ``None``, а интерфейс попросит заполнить поле.
    """
    if not admission_year:
        return None
    years_passed = session_.year_start - admission_year
    if years_passed < 0:
        return None
    semester = years_passed * 2 + (1 if session_.term == Term.AUTUMN else 2)
    return semester if 1 <= semester <= 12 else None


def demands_of(session: Session, session_id: int | None = None):
    """Запрос нагрузки текущего периода.

    Строки без периода тоже попадают: они остались от времён, когда
    периода не было, и прятать их значило бы потерять данные молча.
    Правильное место их починить — страница нагрузки, а не запрос.
    """
    from schedule_maker.models import LessonDemand

    target = session_id if session_id is not None else current_session(session).id
    return select(LessonDemand).where(
        (LessonDemand.session_id == target) | (LessonDemand.session_id.is_(None))
    )


def versions_of(session: Session, session_id: int | None = None):
    """Запрос версий расписания текущего периода."""
    from schedule_maker.models import ScheduleVersion

    target = session_id if session_id is not None else current_session(session).id
    return select(ScheduleVersion).where(
        (ScheduleVersion.session_id == target) | (ScheduleVersion.session_id.is_(None))
    )
